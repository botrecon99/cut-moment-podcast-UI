import numpy as np
from scipy.io import wavfile
import tensorflow as tf
import io

import audioset.vggish_input as vggish_input
import audioset.vggish_params as vggish_params
import audioset.vggish_postprocess as vggish_postprocess
import audioset.vggish_slim as vggish_slim

# Sửa TF 2.x
tf.compat.v1.disable_eager_execution()

PCA_PARAMS = 'audioset/vggish_pca_params.npz'
VGG_CHECKPOINT = 'audioset/vggish_model.ckpt'

class VGGishEmbedder(object):
    def __init__(self, tfrecord_file=None):
        self.pproc = vggish_postprocess.Postprocessor(PCA_PARAMS)

        self.writer = tf.io.TFRecordWriter(tfrecord_file) if tfrecord_file else None

        self.graph = tf.Graph()
        with self.graph.as_default():
            self.sess = tf.compat.v1.Session()
            sess = self.sess

            vggish_slim.define_vggish_slim(training=False)
            vggish_slim.load_vggish_slim_checkpoint(sess, VGG_CHECKPOINT)

            self.features_tensor = sess.graph.get_tensor_by_name(vggish_params.INPUT_TENSOR_NAME)
            self.embedding_tensor = sess.graph.get_tensor_by_name(vggish_params.OUTPUT_TENSOR_NAME)

    def convert_audio_to_embedding(self, wav_file):
        if not wav_file:
            num_secs = 5
            freq = 1000
            sr = 44100
            t = np.linspace(0, num_secs, int(num_secs * sr))
            x = np.sin(2 * np.pi * freq * t)
            samples = np.clip(x * 32768, -32768, 32767).astype(np.int16)
            wav_file = io.BytesIO()
            wavfile.write(wav_file, sr, samples)
            wav_file.seek(0)

        examples_batch = vggish_input.wavfile_to_examples(wav_file)
        return self.convert_examples_to_embedding(examples_batch)

    def convert_waveform_to_embedding(self, waveform, sample_rate):
        samples = waveform / 32768.0  # Convert to [-1.0, +1.0]
        examples_batch = vggish_input.waveform_to_examples(samples, sample_rate)
        return self.convert_examples_to_embedding(examples_batch)

    def convert_examples_to_embedding(self, examples_batch):
        sess = self.sess
        [embedding_batch] = sess.run(
            [self.embedding_tensor],
            feed_dict={self.features_tensor: examples_batch}
        )
        postprocessed_batch = self.pproc.postprocess(embedding_batch)

        if self.writer:
            seq_example = tf.train.SequenceExample(
                feature_lists=tf.train.FeatureLists(
                    feature_list={
                        vggish_params.AUDIO_EMBEDDING_FEATURE_NAME: tf.train.FeatureList(
                            feature=[
                                tf.train.Feature(
                                    bytes_list=tf.train.BytesList(
                                        value=[embedding.tobytes()]
                                    )
                                ) for embedding in postprocessed_batch
                            ]
                        )
                    }
                )
            )
            self.writer.write(seq_example.SerializeToString())
            self.writer.close()

        return postprocessed_batch
