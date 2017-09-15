#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
file: mnist_acgan.py
author: Karan Singhal 

Train an Auxiliary Classifier Generative Adversarial Network (ACGAN) on the
CROHME dataset. See https://arxiv.org/abs/1610.09585 for more details. Adapted
much of this code from Luke de Oliveira (lukedeo@vaitech.io). Beyond changing the 
architecture of the model for the new dataset, my modifications were in improving the 
performance of the model, mainly by avoiding needless replication of the 
discriminator model. See [this blog post]
(https://ctmakro.github.io/site/on_learning/fast_gan_in_keras.html) 
for more information on the issue, and why it plagues nearly every implementation of GANs 
with Keras. Also, some extra utilities for visualizing/interpreting results of training 
for my needs were added.

You should start to see reasonable images after ~5 epochs, and good images
by ~15 epochs. You should use a GPU, as the convolution-heavy operations are
very slow on the CPU. Prefer the TensorFlow backend if you plan on iterating, as
the compilation time can be a blocker using Theano.

Timings:

Hardware           | Backend | Time / Epoch
-------------------------------------------
 CPU               | TF      | 3 hrs
 Titan X (maxwell) | TF      | 4 min
 Titan X (maxwell) | TH      | 7 min
"""
from collections import defaultdict
import pickle
from PIL import Image

from six.moves import range

import keras.backend as K
from keras.datasets import mnist
from keras.layers import Input, Dense, Reshape, Flatten, Embedding, multiply, Dropout
from keras.layers.advanced_activations import LeakyReLU
from keras.layers.convolutional import UpSampling2D, Convolution2D
from keras.models import Sequential, Model
from keras.optimizers import Adam
from keras.utils.generic_utils import Progbar
import numpy as np
import os
import image_utils
from glob import glob

np.random.seed(1337)

K.set_image_dim_ordering('th')

### TENSORPORT SUPPORT

from tensorport import get_logs_path

outputs_path = get_logs_path(root="outputs/")

###

num_classes = image_utils.NUM_CLASSES
image_size = 28

def get_weighted_class(classes_distribution, size=None):
    return np.random.choice(range(len(classes_distribution)), p=classes_distribution, size=size)


def build_generator(latent_size):
    # we will map a pair of (z, L), where z is a latent vector and L is a
    # label drawn from P_c, to image space (..., 1, image_size, image_size)
    cnn = Sequential()

    cnn.add(Dense(1024, input_dim=latent_size, activation='relu'))
    cnn.add(Dense(128 * 7 * 7, activation='relu'))
    cnn.add(Reshape((128, 7, 7)))

    # upsample to (..., 14, 14)
    cnn.add(UpSampling2D(size=(2, 2)))
    cnn.add(Convolution2D(256, 5, 5, border_mode='same',
                          activation='relu', init='glorot_normal'))

    # upsample to (..., 28, 28)
    cnn.add(UpSampling2D(size=(2, 2)))
    cnn.add(Convolution2D(128, 5, 5, border_mode='same',
                          activation='relu', init='glorot_normal'))

    # take a channel axis reduction
    cnn.add(Convolution2D(1, 2, 2, border_mode='same',
                          activation='tanh', init='glorot_normal'))

    # this is the z space commonly refered to in GAN papers
    latent = Input(shape=(latent_size, ))

    # this will be our label
    image_class = Input(shape=(1,), dtype='int32')

    cls = Flatten()(Embedding(num_classes, latent_size,
                              init='glorot_normal')(image_class))

    # hadamard product between z-space and a class conditional embedding
    h = multiply([latent, cls])

    fake_image = cnn(h)

    print(cnn.summary())

    return Model(input=[latent, image_class], output=fake_image)


def build_discriminator():
    # build a relatively standard conv net, with LeakyReLUs as suggested in
    # the reference paper
    cnn = Sequential()

    cnn.add(Convolution2D(32, 3, 3, border_mode='same', subsample=(2, 2),
                          input_shape=(1, image_size, image_size)))
    cnn.add(LeakyReLU())
    cnn.add(Dropout(0.3))

    cnn.add(Convolution2D(64, 3, 3, border_mode='same', subsample=(1, 1)))
    cnn.add(LeakyReLU())
    cnn.add(Dropout(0.3))

    cnn.add(Convolution2D(128, 3, 3, border_mode='same', subsample=(2, 2)))
    cnn.add(LeakyReLU())
    cnn.add(Dropout(0.3))

    cnn.add(Convolution2D(256, 3, 3, border_mode='same', subsample=(1, 1)))
    cnn.add(LeakyReLU())
    cnn.add(Dropout(0.3))

    cnn.add(Flatten())

    image = Input(shape=(1, image_size, image_size))

    features = cnn(image)

    print(cnn.summary())

    # first output (name=generation) is whether or not the discriminator
    # thinks the image that is being shown is fake, and the second output
    # (name=auxiliary) is the class that the discriminator thinks the image
    # belongs to.
    fake = Dense(1, activation='sigmoid', name='generation')(features)
    aux = Dense(num_classes, activation='softmax', name='auxiliary')(features)

    return Model(input=image, output=[fake, aux])

if __name__ == '__main__':

    if not os.path.exists(outputs_path):
        os.makedirs(outputs_path)

    # batch and latent size taken from the paper
    nb_epochs = 50
    batch_size = 100
    latent_size = 100

    # Adam parameters suggested in https://arxiv.org/abs/1511.06434
    adam_lr = 0.00005
    adam_beta_1 = 0.5

    # build the discriminator
    discriminator = build_discriminator()
    print(discriminator.summary())
    discriminator.compile(
        optimizer=Adam(lr=adam_lr, beta_1=adam_beta_1),
        loss=['binary_crossentropy', 'sparse_categorical_crossentropy']
    )

    # build the generator
    generator = build_generator(latent_size)
    print(generator.summary())
    generator.compile(optimizer=Adam(lr=adam_lr * 1.5, beta_1=adam_beta_1),
                      loss='binary_crossentropy')

    latent = Input(shape=(latent_size, ))
    image_class = Input(shape=(1,), dtype='int32')

    # get a fake image
    fake_image = generator([latent, image_class])

    # we only want to be able to train generation for the combined model
    discriminator.trainable = False
    fake, aux = discriminator(fake_image)
    combined = Model(input=[latent, image_class], output=[fake, aux])

    combined.compile(
        optimizer=Adam(lr=adam_lr * 1.5, beta_1=adam_beta_1),
        loss=['binary_crossentropy', 'sparse_categorical_crossentropy']
    )

    generator_filenames = sorted(glob('params_generator*'))
    discriminator_filenames = sorted(glob('params_discriminator*'))

    if generator_filenames and discriminator_filenames:
        print("Loading models from file!")
        # discriminator.layers[-2:-1] = [Concatenate(discriminator.layers[-1]), Concatenate(discriminator.layers[-2])]
        # print(discriminator.layers)
        generator.load_weights(generator_filenames[-1])
        discriminator.load_weights(discriminator_filenames[-1])
        print("Loaded models from file")

    # get our mnist data, and force it to be of shape (..., 1, 28, 28) with
    # range [-1, 1]
    X_train, y_train, X_test, y_test, classes_distribution = image_utils.get_math_dataset()
    print("Data shapes")
    print(X_train.shape, y_train.shape, X_test.shape, y_test.shape)

    nb_train, nb_test = X_train.shape[0], X_test.shape[0]

    train_history = defaultdict(list)
    test_history = defaultdict(list)

    for epoch in range(nb_epochs):
        print('Epoch {} of {}'.format(epoch + 1, nb_epochs))

        nb_batches = int(X_train.shape[0] / batch_size)
        progress_bar = Progbar(target=nb_batches)

        epoch_gen_loss = []
        epoch_disc_loss = []

        for index in range(nb_batches):
            progress_bar.update(index)
            # generate a new batch of noise
            noise = np.random.uniform(-1, 1, (batch_size, latent_size))

            # get a batch of real images
            image_batch = X_train[index * batch_size:(index + 1) * batch_size]
            label_batch = y_train[index * batch_size:(index + 1) * batch_size]

            # sample some labels from p_c
            sampled_labels = get_weighted_class(classes_distribution, batch_size)

            # generate a batch of fake images, using the generated labels as a
            # conditioner. We reshape the sampled labels to be
            # (batch_size, 1) so that we can feed them into the embedding
            # layer as a length one sequence
            generated_images = generator.predict(
                [noise, sampled_labels.reshape((-1, 1))], verbose=0)

            X = np.concatenate((image_batch, generated_images))
            y = np.array([1] * batch_size + [0] * batch_size)
            aux_y = np.concatenate((label_batch, sampled_labels), axis=0)

            # see if the discriminator can figure itself out...
            epoch_disc_loss.append(discriminator.train_on_batch(X, [y, aux_y]))

            # make new noise. we generate 2 * batch size here such that we have
            # the generator optimize over an identical number of images as the
            # discriminator
            noise = np.random.uniform(-1, 1, (2 * batch_size, latent_size))
            sampled_labels = get_weighted_class(classes_distribution, 2 * batch_size)

            # we want to train the generator to trick the discriminator
            # For the generator, we want all the {fake, not-fake} labels to say
            # not-fake
            trick = np.ones(2 * batch_size)

            epoch_gen_loss.append(combined.train_on_batch(
                [noise, sampled_labels.reshape((-1, 1))], [trick, sampled_labels]))

        print('\nTesting for epoch {}:'.format(epoch + 1))

        # evaluate the testing loss here

        # generate a new batch of noise
        noise = np.random.uniform(-1, 1, (nb_test, latent_size))

        # sample some labels from p_c and generate images from them
        sampled_labels = get_weighted_class(classes_distribution, nb_test)
        generated_images = generator.predict(
            [noise, sampled_labels.reshape((-1, 1))], verbose=False)

        X = np.concatenate((X_test, generated_images))
        y = np.array([1] * nb_test + [0] * nb_test)
        aux_y = np.concatenate((y_test, sampled_labels), axis=0)

        # see if the discriminator can figure itself out...
        discriminator_test_loss = discriminator.evaluate(
            X, [y, aux_y], verbose=False)

        discriminator_train_loss = np.mean(np.array(epoch_disc_loss), axis=0)

        # make new noise
        noise = np.random.uniform(-1, 1, (2 * nb_test, latent_size))
        sampled_labels = get_weighted_class(classes_distribution, 2 * nb_test)

        trick = np.ones(2 * nb_test)

        generator_test_loss = combined.evaluate(
            [noise, sampled_labels.reshape((-1, 1))],
            [trick, sampled_labels], verbose=False)

        generator_train_loss = np.mean(np.array(epoch_gen_loss), axis=0)

        # generate an epoch report on performance
        train_history['generator'].append(generator_train_loss)
        train_history['discriminator'].append(discriminator_train_loss)

        test_history['generator'].append(generator_test_loss)
        test_history['discriminator'].append(discriminator_test_loss)

        print('{0:<22s} | {1:4s} | {2:15s} | {3:5s}'.format(
            'component', *discriminator.metrics_names))
        print('-' * 65)

        ROW_FMT = '{0:<22s} | {1:<4.2f} | {2:<15.2f} | {3:<5.2f}'
        print(ROW_FMT.format('generator (train)',
                             *train_history['generator'][-1]))
        print(ROW_FMT.format('generator (test)',
                             *test_history['generator'][-1]))
        print(ROW_FMT.format('discriminator (train)',
                             *train_history['discriminator'][-1]))
        print(ROW_FMT.format('discriminator (test)',
                             *test_history['discriminator'][-1]))

        # save weights every epoch
        generator.save_weights(os.path.join(outputs_path, 'params_generator_epoch_{0:03d}.hdf5'.format(epoch)), True)
        discriminator.save_weights(os.path.join(outputs_path, 
            'params_discriminator_epoch_{0:03d}.hdf5'.format(epoch)), True)

        digits_to_display = 10

        # generate some digits to display
        noise = np.random.uniform(-1, 1, (digits_to_display * num_classes, latent_size))

        sampled_labels = np.array([
            [i] * digits_to_display for i in range(num_classes)
        ]).reshape(-1, 1)

        # get a batch to display
        generated_images = generator.predict(
            [noise, sampled_labels], verbose=0)

        # arrange them into a grid
        img = (np.concatenate([r.reshape(-1, image_size)
                               for r in np.split(generated_images, digits_to_display)
                               ], axis=-1) * 127.5 + 127.5).astype(np.uint8)

        Image.fromarray(img).save(os.path.join(outputs_path, 
            'plot_epoch_{0:03d}_generated.png'.format(epoch)))

    pickle.dump({'train': train_history, 'test': test_history},
                open(os.path.join(outputs_path, 'acgan-history.pkl'), 'wb'))
