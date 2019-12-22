from torch.utils.data import Dataset
import numpy as np
import torch
from ecg_pytorch.data_reader import pickle_data
from ecg_pytorch.dynamical_model import typical_beat_params, equations
from ecg_pytorch.data_reader import ecg_mit_bih
import logging


class EcgHearBeatsDataset(Dataset):
    """ECG heart beats dataset."""

    def __init__(self, transform=None, beat_type=None, one_vs_all=None, lstm_setting=True):
        """
        [45443, 884, 3536, 414, 8]
        :param transform:
        :param beat_type:
        """
        self.lstm_setting = lstm_setting
        # self.train, self.val, _ = pickle_data.load_ecg_input_from_pickle()
        self.one_vs_all = False
        # self.train = np.concatenate((self.train, self.val), axis=0)
        mit_bih_dataset = ecg_mit_bih.ECGMitBihDataset()
        self.train = mit_bih_dataset.train_heartbeats
        if beat_type is not None and one_vs_all is None:
            self.train = np.array([sample for sample in self.train if sample['aami_label_str'] == beat_type])

        if one_vs_all is not None:
            self.beat_type = beat_type
            self.one_vs_all = True
            self.num_of_classes = 2
        else:
            self.num_of_classes = 5

        # consts:
        self.transform = transform
        self.beat_types = ['N', 'S', 'V', 'F', 'Q']
        self.beat_type_to_one_hot_label = {'N': [1, 0, 0, 0, 0],
                                           'S': [0, 1, 0, 0, 0],
                                           'V': [0, 0, 1, 0, 0],
                                           'F': [0, 0, 0, 1, 0],
                                           'Q': [0, 0, 0, 0, 1]}

    def make_weights_for_balanced_classes(self):
        count = [self.len_beat('N'), self.len_beat('S'), self.len_beat('V'),
                 self.len_beat('F'), self.len_beat('Q')]
        weight_per_class = [0.] * self.num_of_classes
        N = float(sum(count))
        for i in range(self.num_of_classes):
            weight_per_class[i] = N / float(count[i])
        weight = [0] * len(self.train)
        for idx, val in enumerate(self.train):
            label_ind = val['aami_label_ind']
            weight[idx] = weight_per_class[label_ind]
        return weight

    def weights_per_class(self):
        count = np.array([self.len_beat('N'), self.len_beat('S'), self.len_beat('V'),
                 self.len_beat('F'), self.len_beat('Q')])
        print("Beat N: #{}\t Beat S: #{}\t Beat V: #{}\n Beat F: #{}\t Beat Q: #{}".format(count[0], count[1], count[2],
                                                                                           count[3], count[4]))
        N = float(sum(count))
        print("Total num of beats: #{}".format(N))
        weights = N / count
        print(weights)
        return weights

    def __len__(self):
        return len(self.train)

    def len_beat(self, beat_Type):
        return len(np.array([sample for sample in self.train if sample['aami_label_str'] == beat_Type]))

    def __getitem__(self, idx):
        sample = self.train[idx]

        if self.lstm_setting:
            lstm_beat = np.array([sample['cardiac_cycle'][i:i + 5] for i in range(0, 215, 5)])
        else:
            lstm_beat = sample['cardiac_cycle']
        tag = sample['aami_label_str']
        if not self.one_vs_all:
            sample = {'cardiac_cycle': lstm_beat, 'beat_type': tag, 'label': np.array(sample['aami_label_one_hot'])}
        else:
            if tag == self.beat_type:
                sample = {'cardiac_cycle': lstm_beat, 'beat_type': tag, 'label': np.array([1, 0])}
            else:
                sample = {'cardiac_cycle': lstm_beat, 'beat_type': tag, 'label': np.array([0, 1])}
        if self.transform:
            sample = self.transform(sample)
        return sample

    def add_beats_from_generator(self, generator_model, num_beats_to_add, checkpoint_path, beat_type):
        logging.info("Adding data from generator: {}. number of beats to add: {}\t"
                     "checkpoint path: {}\t beat type: {}".format(generator_model, num_beats_to_add, checkpoint_path,
                                                                  beat_type))
        checkpoint = torch.load(checkpoint_path)
        generator_model.load_state_dict(checkpoint['generator_state_dict'])
        # discriminator_model.load_state_dict(checkpoint['discriminator_state_dict'])
        with torch.no_grad():
            input_noise = torch.Tensor(np.random.normal(0, 1, (num_beats_to_add, 100)))
            output_g = generator_model(input_noise)
            output_g = output_g.numpy()
            output_g = np.array(
                [{'cardiac_cycle': x, 'aami_label_str': beat_type, 'aami_label_one_hot': self.beat_type_to_one_hot_label[beat_type]} for x
                 in output_g])
            # plt.plot(output_g[0]['cardiac_cycle'])
            # plt.show()
            self.additional_data_from_gan = output_g
            self.train = np.concatenate((self.train, output_g))
            print("Length of train samples after adding from generator is {}".format(len(self.train)))

    def add_beats_from_simulator(self, num_beats_to_add, beat_type):
        beat_params = typical_beat_params.beat_type_to_typical_param[beat_type]
        noise_param = (np.random.normal(0, 0.1, (num_beats_to_add, 15)))
        params = 0.01 * noise_param + beat_params
        sim_beats = equations.generate_batch_of_beats_numpy(params)
        sim_beats = np.array(
            [{'cardiac_cycle': x, 'beat_type': beat_type, 'label': self.beat_type_to_one_hot_label[beat_type]} for x
             in sim_beats])
        self.additional_data_from_simulator = sim_beats
        self.train = np.concatenate((self.train, sim_beats))
        print("Length of train samples after adding from simulator is {}".format(len(self.train)))
        return sim_beats

    def add_noise(self, n, beat_type):
        input_noise = np.random.normal(0, 1, (n, 216))

        input_noise = np.array(
            [{'cardiac_cycle': x, 'beat_type': beat_type, 'label': self.beat_type_to_one_hot_label[beat_type]} for x
             in input_noise])
        self.train = np.concatenate((self.train, input_noise))


class Scale(object):
    def __call__(self, sample):
        heartbeat, label = sample['cardiac_cycle'], sample['label']
        heartbeat = scale_signal(heartbeat)
        return {'cardiac_cycle': heartbeat,
                'label': label,
                'beat_type': sample['beat_type']}


def scale_signal(signal, min_val=-0.01563, max_val=0.042557):
    """

    :param min:
    :param max:
    :return:
    """
    # Scale signal to lie between -0.4 and 1.2 mV :
    scaled = np.interp(signal, (signal.min(), signal.max()), (min_val, max_val))

    # zmin = min(signal)
    # zmax = max(signal)
    # zrange = zmax - zmin
    # # for (i=1; i <= Nts; i++)
    # scaled = [(z - zmin) * max_val / zrange + min_val for z in signal]
    return scaled


class EcgHearBeatsDatasetTest(Dataset):
    """ECG heart beats dataset."""

    def __init__(self, transform=None, beat_type=None, one_vs_all=None, lstm_setting=True):
        #  _, _, self.test = pickle_data.load_ecg_input_from_pickle()
        mit_bih_dataset = ecg_mit_bih.ECGMitBihDataset()
        self.train = mit_bih_dataset.train_heartbeats
        self.test = mit_bih_dataset.test_heartbeats

        self.test = self.test + self.train
        self.lstm_setting = lstm_setting
        self.one_vs_all = False
        if beat_type is not None and one_vs_all is None:
            self.test = np.array([sample for sample in self.test if sample['aami_label_str'] == beat_type])

        if one_vs_all is not None:
            self.beat_type = beat_type
            self.one_vs_all = True
            self.num_of_classes = 2
        else:
            self.num_of_classes = 5
        self.transform = transform

    def __len__(self):
        return len(self.test)

    def __getitem__(self, idx):
        sample = self.test[idx]

        if self.lstm_setting:
            lstm_beat = np.array([sample['cardiac_cycle'][i:i + 5] for i in range(0, 215, 5)])  # [43, 5]
        else:
            lstm_beat = sample['cardiac_cycle']
        tag = sample['aami_label_str']
        # sample = {'cardiac_cycle': lstm_beat, 'beat_type': tag, 'label': np.array(sample['label'])}
        if not self.one_vs_all:
            sample = {'cardiac_cycle': lstm_beat, 'beat_type': tag, 'label': np.array(sample['aami_label_one_hot'])}
        else:
            if tag == self.beat_type:
                sample = {'cardiac_cycle': lstm_beat, 'beat_type': tag, 'label': np.array([1, 0])}
            else:
                sample = {'cardiac_cycle': lstm_beat, 'beat_type': tag, 'label': np.array([0, 1])}
        if self.transform:
            sample = self.transform(sample)
        return sample

    def len_beat(self, beat_Type):
        return len(np.array([sample for sample in self.test if sample['aami_label_str'] == beat_Type]))

    def print_statistics(self):
        count = np.array([self.len_beat('N'), self.len_beat('S'), self.len_beat('V'),
                          self.len_beat('F'), self.len_beat('Q')])
        print("Beat N: #{}\t Beat S: #{}\t Beat V: #{}\n Beat F: #{}\t Beat Q: #{}".format(count[0], count[1], count[2],
                                                                                           count[3], count[4]))


class ToTensor(object):
    """Convert ndarrays in sample to Tensors."""

    def __call__(self, sample):
        heartbeat, label = sample['cardiac_cycle'], sample['label']
        return {'cardiac_cycle': (torch.from_numpy(heartbeat)).double(),
                'label': torch.from_numpy(label),
                'beat_type': sample['beat_type']}


if __name__ == "__main__":
    test_set = EcgHearBeatsDatasetTest()
    test_set.print_statistics()