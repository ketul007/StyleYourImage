import numpy as np
from keras.applications import vgg19
from keras.preprocessing.image import load_img, img_to_array
from scipy.optimize import fmin_l_bfgs_b
from scipy.misc import imsave
from keras import backend as K
from flask import Flask, render_template, request
import os

app = Flask(__name__)
app.debug = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config["CACHE_TYPE"] = "null"

f = ""

#Add your uploaded image to templates folder
UPLOAD_FOLDER = os.path.basename('templates')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


@app.route('/')
def hello_world():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    global f
    file = request.files['image']
    f = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(f)
    main()
    return render_template('home.html')
    

def main():
    global f
    #change image size to 400-400
    def preprocess_image(image_path, height=None, width=None):
        height = 400 if not height else height
        width = width if width else int(width * height / height)
        img = load_img(image_path, target_size=(height, width))
        img = img_to_array(img)
        img = np.expand_dims(img, axis=0)
        img = vgg19.preprocess_input(img)
        return img
    
    def deprocess_image(x):
        # Remove zero-center by mean pixel
        x[:, :, 0] += 103.939
        x[:, :, 1] += 116.779
        x[:, :, 2] += 123.68
        # 'BGR'->'RGB'
        x = x[:, :, ::-1]
        x = np.clip(x, 0, 255).astype('uint8')
        return x


    TARGET_IMG = f

    REFERENCE_STYLE_IMG = 'static/style1.png'

    width, height = load_img(TARGET_IMG).size
    img_height = 320
    img_width = int(width * img_height / height)


    target_image = K.constant(preprocess_image(TARGET_IMG, height=img_height, width=img_width))
    style_image = K.constant(preprocess_image(REFERENCE_STYLE_IMG, height=img_height, width=img_width))


    generated_image = K.placeholder((1, img_height, img_width, 3))


    input_tensor = K.concatenate([target_image,
                              style_image,
                              generated_image], axis=0)


    model = vgg19.VGG19(input_tensor=input_tensor,
                    weights='imagenet',
                    include_top=False)


    layers = dict([(layer.name, layer.output) for layer in model.layers])

    #There are Three Different types of losses in this context.
    # 1. Content loss : which compare generated image with orginal image and compute loss
    # 2. Style loss : Style loss is based on most popular gram matrix which compute loss for generated image with original style image.
    # 3. Total loss : Content loss + Style loss
    def content_loss(base, combination):
        return K.sum(K.square(combination - base))


    def style_loss(style, combination, height, width):
        #gram matrix is a important matrix which posses weight of layers
        #gram matrix is created by mutlipling weights matrix and it's transpose
        def build_gram_matrix(x):
            features = K.batch_flatten(K.permute_dimensions(x, (2, 0, 1)))
            gram_matrix = K.dot(features, K.transpose(features))
            return gram_matrix

        S = build_gram_matrix(style)
        C = build_gram_matrix(combination)
        channels = 3
        size = height * width
        return K.sum(K.square(S - C)) / (4. * (channels ** 2) * (size ** 2))


    def total_variation_loss(x):
        a = K.square(
            x[:, :img_height - 1, :img_width - 1, :] - x[:, 1:, :img_width - 1, :])
        b = K.square(
            x[:, :img_height - 1, :img_width - 1, :] - x[:, :img_height - 1, 1:, :])
        return K.sum(K.pow(a + b, 1.25))

    content_weight = 0.025
    style_weight = 1.0
    total_variation_weight = 1e-4
    
    # Three different types of neural networks.
    # gatys is a popular technique suggest by Leon A. gatys
    
    def set_cnn_layers(source='gatys'):
        if source == 'gatys':
            content_layer = 'block5_conv2'
            style_layers = ['block1_conv1', 'block2_conv1', 'block3_conv1', 
                        'block4_conv1', 'block5_conv1']
        elif source == 'johnson':
            content_layer = 'block2_conv2'
            style_layers = ['block1_conv2', 'block2_conv2', 'block3_conv3', 
                        'block4_conv3', 'block5_conv3']
        else:
            content_layer = 'block5_conv2'
            style_layers = ['block1_conv1', 'block2_conv1', 'block3_conv1', 
                        'block4_conv1', 'block5_conv1']
        return content_layer, style_layers


    source_paper = 'gatys'
    content_layer, style_layers = set_cnn_layers(source=source_paper)

    loss = K.variable(0.)

    # add content loss
    layer_features = layers[content_layer]
    target_image_features = layer_features[0, :, :, :]
    combination_features = layer_features[2, :, :, :]
    loss += content_weight * content_loss(target_image_features,
                                          combination_features)

    # add style loss
    for layer_name in style_layers:
        layer_features = layers[layer_name]
        style_reference_features = layer_features[1, :, :, :]
        combination_features = layer_features[2, :, :, :]
        sl = style_loss(style_reference_features, combination_features, 
                        height=img_height, width=img_width)
        loss += (style_weight / len(style_layers)) * sl

    # add total variation loss
    loss += total_variation_weight * total_variation_loss(generated_image)


    # Get the gradients of the generated image wrt the loss
    grads = K.gradients(loss, generated_image)[0]

    # Function to fetch the values of the current loss and the current gradients
    fetch_loss_and_grads = K.function([generated_image], [loss, grads])


    class Evaluator(object):

        def __init__(self, height=None, width=None):
            self.loss_value = None
            self.grads_values = None
            self.height = height
            self.width = width

        def loss(self, x):
            assert self.loss_value is None
            x = x.reshape((1, self.height, self.width, 3))
            outs = fetch_loss_and_grads([x])
            loss_value = outs[0]
            grad_values = outs[1].flatten().astype('float64')
            self.loss_value = loss_value
            self.grad_values = grad_values
            return self.loss_value

        def grads(self, x):
            assert self.loss_value is not None
            grad_values = np.copy(self.grad_values)
            self.loss_value = None
            self.grad_values = None
            return grad_values

    evaluator = Evaluator(height=img_height, width=img_width)



    iterations = 1

    x = preprocess_image(TARGET_IMG, height=img_height, width=img_width)
    x = x.flatten()

    for i in range(iterations):
        x, min_val, info = fmin_l_bfgs_b(evaluator.loss, x,
                                         fprime=evaluator.grads, maxfun=20)
        print('Current loss value:', min_val)
        img = x.copy().reshape((img_height, img_width, 3))
        img = deprocess_image(img)
        fname = "static/created2.png"
        imsave(fname, img)
        print('Image saved as', fname)
        

if __name__ == "__main__":
    app.jinja_env.auto_reload = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.run(debug=True)
    
