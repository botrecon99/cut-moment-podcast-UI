import tensorflow.compat.v1 as tf
import tf_slim as slim  # Đã tách riêng tf_slim
import audioset.vggish_params as params

tf.disable_eager_execution()  # Đảm bảo hoạt động trong chế độ graph cũ

def define_vggish_slim(training=False):
    """Defines the VGGish TensorFlow model."""
    with slim.arg_scope([slim.conv2d, slim.fully_connected],
                        weights_initializer=tf.truncated_normal_initializer(stddev=params.INIT_STDDEV),
                        biases_initializer=tf.zeros_initializer(),
                        activation_fn=tf.nn.relu,
                        trainable=training), \
         slim.arg_scope([slim.conv2d],
                        kernel_size=[3, 3], stride=1, padding='SAME'), \
         slim.arg_scope([slim.max_pool2d],
                        kernel_size=[2, 2], stride=2, padding='SAME'), \
         tf.variable_scope('vggish'):
        
        features = tf.placeholder(
            tf.float32, shape=(None, params.NUM_FRAMES, params.NUM_BANDS), name='input_features')
        net = tf.reshape(features, [-1, params.NUM_FRAMES, params.NUM_BANDS, 1])

        net = slim.conv2d(net, 64, scope='conv1')
        net = slim.max_pool2d(net, scope='pool1')
        net = slim.conv2d(net, 128, scope='conv2')
        net = slim.max_pool2d(net, scope='pool2')
        net = slim.repeat(net, 2, slim.conv2d, 256, scope='conv3')
        net = slim.max_pool2d(net, scope='pool3')
        net = slim.repeat(net, 2, slim.conv2d, 512, scope='conv4')
        net = slim.max_pool2d(net, scope='pool4')

        net = slim.flatten(net)
        net = slim.repeat(net, 2, slim.fully_connected, 4096, scope='fc1')
        net = slim.fully_connected(net, params.EMBEDDING_SIZE, scope='fc2')

        return tf.identity(net, name='embedding')


def load_vggish_slim_checkpoint(session, checkpoint_path):
    """Loads a pre-trained VGGish-compatible checkpoint."""
    with tf.Graph().as_default():
        define_vggish_slim(training=False)
        vggish_var_names = [v.name for v in tf.global_variables()]

    vggish_vars = [v for v in tf.global_variables() if v.name in vggish_var_names]
    saver = tf.train.Saver(vggish_vars, name='vggish_load_pretrained')
    saver.restore(session, checkpoint_path)
