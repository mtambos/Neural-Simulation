## -*- coding: utf-8 -*-
################################################################################
##
##  Copyright 2010 Philipp Meier <pmeier82@googlemail.com>
##
##  Licensed under the EUPL, Version 1.1 or – as soon they will be approved by
##  the European Commission - subsequent versions of the EUPL (the "Licence");
##  You may not use this work except in compliance with the Licence.
##  You may obtain a copy of the Licence at:
##
##  http://ec.europa.eu/idabc/eupl
##
##  Unless required by applicable law or agreed to in writing, software
##  distributed under the Licence is distributed on an "AS IS" basis,
##  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
##  See the Licence for the specific language governing permissions and
##  limitations under the Licence.
##
################################################################################
#
# nsim - simulation.py
#
# Philipp Meier - <pmeier82 at googlemail dot com>
# 2010-01-21
#

"""simulation framework for extracellular recordings

This module provides a discrete time event simulation framework for objects
placed in a 3-dimensional space (refered to as the scene). Objects have a
distinct position in the scene for each point in time. Time is descretized to
samples with a fixed sample rate. This way the realtime/observed simulation
speed is decoupled from the internal time scale and can be changed on the fly.

Objets are classified as either Recorders or Neurons and are modeled as
subclasses of SimObject. A SimObject is a set of scene coordinates, along with a
set of applicable behaviors. E.g. Neurons can have a firing statistic.

The simulation is conducted in frames. A Frame is a batch of samples that is
simulated as one step of the simulation. Any input to the simulation while a
frame is processed is queued and applied at the end of the frame (applies to
movement etc.).

The framework is tailored towards simulation of extracellular recordings with
several (1 to 10) Recorders and clusters of Neurons (3 clusters, 10 each).

Recorded data can be either saved to files (HDF5) or broadcasted to the net via
TCP/IP with a simple protocoll.

Definition of terms:
:SCENE:
    The SCENE is the spatial context of the simulation (usually a 3D space with
    euclidean coordinates). Time is advanced with respect to a fixed sampling
    rate and the SCENE has a destinct configuration per sample. There is only
    one SCENE.
:OBJECT:
    An OBJECT is a subclass of SimObject. OBJECTs model all agents and sources
    in the SCENE, they are administrated as items of the BaseSimulation instance
    .
:SAMPLE:
    A SAMPLE is the smallest discrete time step known to the simulation. It is
    thus the unit of measurement for the temporal sampling. A SAMPLE i related
    to real time via the SAMPLE_RATE, denoting the SAMPLEs in a second (Hz).
:FRAME:
    A FRAME is a fixed time period of n samples. The simulation is advanced in
    frames. It is computationally attractive to simulate FRAMEs as oposed to
    single SAMPLEs, as most numerical algorithms perform better on batch data
    than on single SAMPLEs. Also timer implementations of normal systems may not
    provide the resolution neccessary to simulate common sampling rates (like
    16kHz or 32kHz) in real time.
"""
__docformat__ = 'restructuredtext'


##---IMPORTS

from ConfigParser import ConfigParser
import os.path as osp
from cluster_dynamics import ClusterDynamics
from data_io import SimIOManager, SimPkg
from scene import (
    NeuronDataContainer,
    Neuron,
    Recorder,
    SimObject,
    Tetrode
)


##---VERSION

try:
    __version__ = 'rev. %s - %s %s' % tuple(
        '$Id: simulation.py 4857 2010-06-09 14:59:29Z phil $'.split(' ')[2:5]
    )
except:
    __version__ = 'unknown'


##---CONSTANTS

MOVEMENT_TOL = 1e-5


##---CLASSES

class SimExternalDelegate(object):
    """delegate class, subclass for specific GUI kit or other interface

    An instance of SimExternalDelegate should pass messages/events on to a
    suitable external interface, like a GUI kit (frex QT or GTK). As we must not
    make assumptions about the frontend, we provide this delegate class for the
    frontend to receive events.
    """

    ## constructor

    def __init__(self, **kwargs):
        """
        :Parameters:
            kwargs : dict
                Keywords for subclasses
        """

        pass

    ## event delegate methods

    def log(self, log_str):
        """log a sting"""
        pass

    def frame(self, frame):
        """update frame"""
        pass

    def frame_size(self, frame_size):
        """update frame size"""
        pass

    def sample(self, sample):
        """update sample"""
        pass

    def sample_rate(self, sample_rate):
        """update sample rate"""
        pass

    def info_internal(self, info):
        """update internal infos"""
        pass

    def info_ndata(self, info):
        """update neuron data infos"""
        pass

    def info_neuron(self, info):
        """update neuron infos"""
        pass

    def info_recorder(self, info):
        """update recorder infos"""
        pass

    def info_complete(self, info):
        """update complete infos"""
        pass


class BaseSimulation(dict):
    """simulation class controlling the scene"""

    ## constructor

    def __init__(self, **kwargs):
        """
        :Parameters:
            kwargs : dict
                Keyword arguments, see Keywords.
        :Keywords:
            debug : bool
                Debug output toggle.
                Default=False
            externals : list
                A list of SimExternalDelegate instances. Internal state changes
                will be propagated to the delegates.
                Default=False
            sample_rate : float
                Sample rate.
            frame : int
                Frame to start at.
            frame_size : int
                Frame size.
            cfg : str
                Path to a config file, readable by a ConfigParser instance.
        """

        # private property members
        self._cfg = None
        self._externals = []
        self._frame = None
        self._frame_size = None
        self._sample_rate = None
        self._status = None

        # public members
        self.cls_dyn = ClusterDynamics()
        self.io_man = SimIOManager()
        self.neuron_data = NeuronDataContainer()
        self.debug = kwargs.get('debug', False)

        # externals
        for ext in kwargs.get('externals', []):
            if not isinstance(ext, SimExternalDelegate):
                continue
            self._externals.append(ext)

    def initialize(self, **kwargs):
        """initialize the simulation

        :Keywords:
            debug : bool
                Debug flag, enables verbose output.
                Default=False
            frame : long
                Frame id to start at.
                Default=0
            frame_size : int
                The frame size.
                Default=16
            sample_rate : float
                Sample rate to operate.
                Default=16000.0
        """

        self.clear()

        # reset private members
        self.sample_rate = kwargs.get('sample_rate', 16000.0)
        self.frame = kwargs.get('frame', 0)
        self.frame_size = kwargs.get('frame_size', 1024)
        self.status

        # reset pubic members
        self.cls_dyn.clear()
        self.io_man.initialize()
        self.neuron_data.clear()

    def finalize(self):
        """finalize the simulation"""

        self.clear()

        # reset pubic members
        self.cls_dyn.clear()
        self.io_man.finalize()
        self.neuron_data.clear()

    ## properties

    def get_frame(self):
        return self._frame
    def set_frame(self, value):
        self._frame = long(value)
        for ext in self._externals:
            ext.frame(self._frame)
    frame = property(get_frame, set_frame)

    def get_frame_size(self):
        return self._frame_size
    def set_frame_size(self, value):
        self._frame_size = int(value)
        for ext in self._externals:
            ext.frame_size(self._frame_size)
        self.status
    frame_size = property(get_frame_size, set_frame_size)

    def get_sample_rate(self):
        return self._sample_rate
    def set_sample_rate(self, value):
        self._sample_rate = float(value)
        self.cls_dyn.sample_rate = self._sample_rate
        for ext in self._externals:
            ext.sample_rate(self._sample_rate)
        self.status
    sample_rate = property(get_sample_rate, set_sample_rate)

    def get_status(self):
        self._status = {
            'frame_size'    : self.frame_size,
            'sample_rate'   : self.sample_rate,
            'neurons'       : self.neuron_keys,
            'recorders'     : self.recorder_keys,
        }
        if self.io_man.is_initialized:
            self.io_man.status = self._status
        return self._status
    status = property(get_status)

    def get_neuron_keys(self):
        return [idx for idx in self if isinstance(self[idx], Neuron)]
    neuron_keys = property(get_neuron_keys)

    def get_recorder_keys(self):
        return [idx for idx in self if isinstance(self[idx], Recorder)]
    recorder_keys = property(get_recorder_keys)

    ## simulation control methods

    def simulate(self):
        """advance the simulation by one frame"""

        # inc frame counter
        self.frame += 1

        # process events
        self._simulate_io_tick()

        # process units
        self._simulate_neuron_tick()

        # record for recorders
        self._simulate_recorder_tick()

    def _simulate_io_tick(self):
        """process io loop for the current frame

        This will tick the SimIOManager and process all queued events.
        """

        # get events
        events = self.io_man.tick()

        while len(events) > 0:

            pkg = events.pop(0)
            print pkg

            log_str = '>>> '

            if pkg.ident in self:

                # recorder event
                if isinstance(self[pkg.ident], Recorder):

                    log_str += 'R[%s]:' % pkg.ident

                    # position event
                    if pkg.tid == SimPkg.T_POS:

                        # position request
                        if pkg.nitems == 0:
                            log_str += 'MOVE[%s] - request' % (self[pkg.ident].name)

                        # reposition request
                        elif pkg.nitems == 1:
                            pos, vel = pkg.cont[0].cont[:2]
                            log_str += 'MOVE: %s, %s' % (pos, vel)
                            self[pkg.ident].trajectory_pos = pos
                            # TODO: implementation of the velocity component

                        # weird position event
                        else:
                            print 'weird event, was T_POS with:', pkg.cont
                            continue

                        # send position acknowledgement
                        self.io_man.send_package(
                            SimPkg.T_POS,
                            pkg.ident,
                            self._frame,
                            self[pkg.ident].trajectory_pos
                        )

                # neuron event
                elif isinstance(self[pkg.ident], Neuron):
                    log_str += 'N:[%s] neuron event' % pkg.ident
                else:
                    log_str += 'ANY:[%s] unknown' % pkg.ident

            # other event
            else:

                log_str += 'O[%s]:' % pkg.ident
                if pkg.tid == SimPkg.T_CON:
                    log_str += 'CONNECT from %s' % str(pkg.cont)
                elif pkg.tid == SimPkg.T_END:
                    log_str += 'DISCONNECT from %s' % str(pkg.cont)
                else:
                    log_str += 'unknown'
            # log
            self.log(log_str)

    def _simulate_neuron_tick(self):
        """process neurons for current frame

        This will generate spike trains and configure the neuronal firing
        behavior for the current frame.
        """

        # generate spike trains for the scene
        self.cls_dyn.generate(self.frame_size)

        # propagate spike trains to neurons
        for nrn_k in self.neuron_keys:
            self[nrn_k].simulate(
                frame_size=self.frame_size,
                firing_times=self.cls_dyn.get_spike_train(nrn_k)
            )

    def _simulate_recorder_tick(self):
        """process recorders for the current frame

        This will record waveforms and grountruth for the current frame.
        """

        # list of all neurons
        nlist = [self[nrn_k] for nrn_k in self.neuron_keys]

        # record per recorder
        for rec_k in self.recorder_keys:
            self.io_man.send_package(
                SimPkg.T_REC,
                rec_k,
                self._frame,
                self[rec_k].simulate(
                    nlist=nlist,
                    frame_size=self.frame_size
                )
            )

    ## methods logging

    def log(self, log_str):
        """log a string"""

        for ext in self._externals:
            ext.log('[#%09d] %s' % (self.frame, log_str))

    def log_d(self, log_str):
        """log a string"""

        if self.debug is True:
            for ext in self._externals:
                ext.log('[DEBUG] %s' % log_str)

    ## methods object management

    def register_neuron(self, **kwargs):
        """register a neuron to the simulation

        :Keywords:
            neuron_data : NeuronData or path or None
                Reference to a NeuronData object or a path to the HDF5 archive
                where the data can be load from.
                CAUTION! str != QString etc.. use str(neuron_data)!
            position : arraylike
                Position in the scene (x,y,z).
                Default=[0,0,0]
            orientation : arraylike
                Orientation of the object. If ndarray it is interpreted as
                direction of orientation relative to the scene's positive
                z-axis. If its a list or tuple, it is interpreted as a triple of
                euler angles relative to the scene's positive z-axis. If it is
                True a random rotation is created.
                Default=False
            rate_of_fire : float
                Rate of fire in Hz.
                Default=50.0
            amplitude : float
                Amplitude of the waveform.
                Default=1.0
            cluster : int
                The cluster idx
        :Raises:
            some error .. mostly ValueErrors for invalid parameters.
        :Returns:
            The string representation of the registered Neuron.
        """

        # check neuron data
        try:
            neuron_data = kwargs.pop('neuron_data')
        except:
            raise ValueError('No "neuron_data" given!')
        if neuron_data in self.neuron_data:
            ndata = self.neuron_data[neuron_data]
        else:
            if not self.neuron_data.insert(neuron_data):
                raise ValueError('Unknown neuron_data: %s' % str(neuron_data))
            else:
                ndata = self.neuron_data[neuron_data]
        kwargs.update(neuron_data=ndata)

        # build neuron
        neuron = Neuron(**kwargs)
        self[id(neuron)] = neuron

        # register in cluster dynamics
        cls_idx = kwargs.get('cluster', None)
        if cls_idx is not None:
            cls_idx = int(cls_idx)
        self.cls_dyn.add_neuron(neuron, cls_idx=cls_idx)

        # log and return
        self.log('>> %s created!' % neuron)
        self.status
        return str(neuron)

    def register_recorder(self, **kwargs):
        """register a recorder for simulation

        :Keywords:
            position : array like
                Position in scene (x,y,z).
                Default=[0,0,0]
            orientation : arraylike or bool
                Orientation of the object. If ndarray it is interpreted as
                direction of orientation relative to the scene's positive
                z-axis. If its a list or tuple, it is interpreted as a triple of
                euler angles relative to the scene's positive z-axis. If it is
                True a random rotation is created.
                Default=False
            scale : float
                Scale factor for the Tetrahedron height. The default uses values
                from the Thomas Recording GmbH website, with a tetrode of about
                40µm height and 20µm for the basis triangle. Note that negative
                values invert the tetrode direction with respect to
                self.position.
                Default=1.0
            snr : float
                Signal to Noise Ratio (SNR) for the noise process.
                Default=1.0
                TODO: fix this value to be congruent with the neuron amplitudes.
            noise_params : list
                The noise generator parameters.
                Default=None
        :Raises:
            some error ..mostly ValueError for invalid parameters.
        :Returns:
            The string representation of the registered Recorder.
        """

        # build tetrode
        tetrode = Tetrode(**kwargs)
        self[id(tetrode)] = tetrode

        # connect and return
        self.log('>> %s created!' % tetrode)
        self.status
        return str(tetrode)

    def remove_object(self, key):
        """remove the SimObject with objectName 'key'

        :Parameters:
            key : SimObject or int/long
                A SimObject or the id of a SimObject attached (as int/long/str).
        :Returns:
            True on successful removal, False else.
        """

        # convert the key
        if isinstance(key, SimObject):
            lookup = id(key)
        elif isinstance(key, (str, unicode, int, long)):
            try:
                lookup = long(key)
            except:
                try:
                    lookup = long(key, 16)
                except:
                    return False
        else:
            return False

        # remove item
        try:
            item = self.pop(lookup)
            self.log('>> %s destroyed!' % item)
            self.status
            return True
        except:
            return False

    def scene_config_load(self, fname):
        """load a scene configuration file

        :Parameters:
            fname : str
                Path to the scene configuration file.
        """

        # checks and inits
        cfg = ConfigParser()
        cfg_check = cfg.read(fname)
        if fname not in cfg_check:
            raise IOError('could not load scene from %s' % fname)

        # check for config
        ndata_paths = cfg.get('CONFIG', 'neuron_data_dir')
        ndata_paths = ndata_paths.strip().split('\n')

        # read per section
        for sec in cfg.sections():

            # check section
            sec_str = sec.split()
            cls = sec_str[0]
            if cls not in ['Neuron', 'Tetrode']:
                continue
            kwargs = {}
            if len(sec_str) > 1:
                kwargs['name'] = ' '.join(sec_str[1:])

            # elaborate class and keyword arguments
            bad_ndata = False
            for k, v in cfg.items(sec):

                # read items
                if k is None or v is None or k == '' or v == '':
                    continue
                elif k in ['position', 'orientation', 'trajectory']:
                    if v == 'False':
                        kwargs[k] = False
                    elif v == 'Truse':
                        kwargs[k] = True
                    else:
                        kwargs[k] = map(float, v.split())
                elif k in ['neuron_data']:
                    kwargs[k] = v
                    ndata_path_list = [osp.join(path, v)
                                       for path in ndata_paths]
                    added_ndata = self.neuron_data.insert(ndata_path_list)
                    if added_ndata == 0:
                        bad_ndata = True
                else:
                    kwargs[k] = v

            # delegate action
            if bad_ndata:
                continue
            elif cls == 'Neuron':
                self.register_neuron(**kwargs)
            elif cls == 'Tetrode':
                self.register_recorder(**kwargs)

    def scene_config_save(self, fname):
        """save the current scene configuration to a file

        :Parameters:
            fname : str
                Path to save tha cfg to.
        """

        # checks and inits
        cfg = ConfigParser()

        # write CONFIG section
        cfg.add_section('CONFIG')
        cfg.set('CONFIG', 'frame_size', self.frame_size)
        cfg.set('CONFIG', 'sample_rate', self.sample_rate)
        ndata_paths = '\t\n'.join(self.neuron_data.paths)
        cfg.set('CONFIG', 'neuron_data_dir', ndata_paths)

        # helper functions
        npy2cfg = lambda x: ' '.join(map(str, x.tolist()))

        # add per SimObject
        for obj in self.values():

            if isinstance(obj, Neuron):

                name = 'Neuron %s' % obj.name
                cfg.add_section(name)
                cfg.set(name, 'cluster', str(self.cls_dyn.get_cls_for_nrn(obj)))
                cfg.set(name, 'position', npy2cfg(obj.position))
                if obj.orientation is True or obj.orientation is False:
                    cfg.set(name, 'orientation', obj.orientation)
                else:
                    cfg.set(name, 'orientation', npy2cfg(obj.orientation))
                cfg.set(name, 'rate_of_fire', str(obj.rate_of_fire))
                cfg.set(name, 'amplitude', str(obj.amplitude))
                cfg.set(name, 'neuron_data', str(obj._neuron_data.filename))

            elif isinstance(obj, Recorder):

                name = 'Tetrode %s' % obj.name
                cfg.add_section(name)
                cfg.set(name, 'position', npy2cfg(obj.position))
                cfg.set(name, 'orientation', npy2cfg(obj._trajectory))
                cfg.set(name, 'snr', str(obj.snr))

        # save
        save_file = open(fname, 'w')
        cfg.write(save_file)
        save_file.close()

    ## methods config loading

    def load_config(self, cfg_path=None):
        """loads initialization values from file

        :Parameters:
            cfg_file : str
                Path to the config file. Should be readable by a ConfigParser.
        """

        # TODO: implement config file handling

    ## special methods

    def __len__(self):
        return len(filter(lambda x: isinstance(x, Neuron), self.values()))
    def __str__(self):
        return 'BaseSimulation :: %d items' % len(self)


if __name__ == '__main__':

    print
    print 'creating BaseSimulation'
    sim = BaseSimulation()
