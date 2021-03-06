#﻿# -*- coding: utf-8 -*-
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
# nsim - data_io/server.py
#
# Philipp Meier - <pmeier82 at googlemail dot com>
# 2010-02-15
#

"""data io classes for the simulation"""
__docformat__ = 'restructuredtext'


##---IMPORTS

# builtins
import logging
from select import select
from threading import Event, Lock, Thread
from Queue import Queue
from SocketServer import BaseRequestHandler, TCPServer, ThreadingMixIn
# packages
import scipy as N
# own imports
from package import SimPkg, recv_pkg, send_pkg


##---MODULE_ADMIN

__all__ = ['SimIOManager', 'SimIOProtocol', 'SimIOServer']


##---CONSTANTS

MAXQUEUESIZE = 1000


##---LOGGING

logging.basicConfig(level=logging.DEBUG)


##---CLASSES

class SimIOProtocol(BaseRequestHandler):
    """the protocoll implementation

    inherited member variables:
    self.request : socket to work with
    self.client_address : (addr,port) of the client
    self.server : server reference
    """

    ## BaseRequestHandler interface

    def setup(self):
        """setup the connection"""

        self.sq_lock = self.server.send_queues_lock
        self.q_recv = self.server.q_recv
        with self.sq_lock:
            self.server.send_queues[self.client_address] = Queue(maxsize=MAXQUEUESIZE)
        self.q_send = self.server.send_queues[self.client_address]
        if self.server.status is not None:
            self.q_send.put(self.server.status)
        self.poll = self.server.client_poll

        logging.info('new connection from %s', str(self.client_address))

    def handle(self):
        """the run method"""

        while True:
            # select
            r, w, e = select([self.request], [], [self.request], self.poll)
            # error
            if len(e) > 0:
                break
            # receive
            if len(r) > 0:
                pkg = recv_pkg(self.request)
                if pkg is None:
                    break
                if pkg.tid in [SimPkg.T_CON, SimPkg.T_END]:
                    pkg.cont = self.client_address
                self.q_recv.put(pkg)
                if pkg.tid == SimPkg.T_END:
                    break
            # send
            with self.sq_lock:
                while not self.q_send.empty():
                    item = self.q_send.get()
                    send_pkg(self.request, item)
                    self.q_send.task_done()

    def finish(self):
        """finalize the connection"""

        logging.debug('closing for %s', str(self.client_address))
        with self.sq_lock:
            if self.client_address in self.server.send_queues:
                q = self.server.send_queues.pop(self.client_address)
                del q


class SimIOServer(ThreadingMixIn, TCPServer, Thread):
    """the server thread spawning one thread per connection"""

    ## class memebers

    allow_reuse_address = True
    request_queue_size = 5
    daemon_threads = True

    ## constructor

    def __init__(
        self,
        server_address,
        q_recv,
        q_send,
        client_poll=0.001
    ):

        # thread super
        Thread.__init__(self, name='SimIOServer')
        self.daemon = True

        # members
        self.q_recv = q_recv    # singleton receive queue
        self.q_send = q_send    # incomming send queue
        self.send_queues = {}
        self.send_queues_lock = Lock()
        self.client_poll = client_poll
        self._status = None
        self._serving = False
        self._is_shutdown = Event()
        self._is_shutdown.set()

        # TCPerver super
        TCPServer.__init__(self, server_address, SimIOProtocol, False)

    ## properties

    def get_status(self):
        return self._status
    def set_status(self, value):
        if not isinstance(value, SimPkg):
            return
        if value != self._status:
            self._status = value
            self.q_send.put(self._status)
    status = property(get_status, set_status)

    ## threading handlers with queue propagation

    def verify_request(self, request, client_address):
        """Verify the request.

        Return True if client_address is not served yet.
        [OVERWRITE]
        """

        if client_address in self.send_queues:
            return False
        else:
            return True

    def propagate_send_queue(self):
        """multiplex items in the send queue to the clients"""

        while not self.q_send.empty():
            item = self.q_send.get()
            with self.send_queues_lock:
                for q in self.send_queues.values():
                    try:
                        q.put(item)
                    except:
                        logging.error('queue was not accessable')
            self.q_send.task_done()

    def run(self):
        """Handle one request at a time until shutdown.

        Polls for shutdown every poll_interval seconds. Ignores
        self.timeout. If you need to do periodic tasks, do them in
        another thread.
        """

        self.server_bind()
        self.server_activate()
        self._serving = True
        self._is_shutdown.clear()
        while self._serving:
            r, w, e = select([self], [], [], 0.5)
            if r:
                self._handle_request_noblock()
            self.propagate_send_queue()
        self.server_close()
        self._is_shutdown.set()

    def stop(self):
        """stop the thread"""

        self._serving = False
        self._is_shutdown.wait()


class SimIOManager(object):
    """the singleton input/output manager thread

    This class handles all data input and output for a BaseSimulation object.
    Data is received from and sent to via Queue.Queue objects using the protocol
    defined in package.py.
    """

    ## constructor

    def __init__(self, **kwargs):
        """
        :Parameters:
            kwargs : keywords
        :Keywords:
            addr : str
                Host address the server binds to.
                Default=all
            port : int
                Host port the server binds to.
                Default=31337
        """

        # members
        self._status = None

        # io members
        self.addr_ini = (kwargs.get('addr', '0.0.0.0'), kwargs.get('port', 31337))
        self.addr = 'not connected'
        self._q_recv = Queue(maxsize=MAXQUEUESIZE)
        self._q_send = Queue(maxsize=MAXQUEUESIZE)
        self._srv = None
        self._is_initialized = False

    def initialize(self):

        self.finalize()
        self._srv = SimIOServer(
            self.addr_ini,
            self._q_recv,
            self._q_send
        )
        self._srv.start()
        self.addr = self._srv.server_address
        self._status = None
        self._is_initialized = True

    def finalize(self):

        if self._srv is not None:
            self._srv.stop()
            self._srv = None
        while not self._q_recv.empty():
            self._q_recv.get_nowait()
        while not self._q_send.empty():
            self._q_send.get_nowait()
        self._is_initialized = False

    ## properties

    def get_status(self):
        return self._status
    def set_status(self, value):
        if not isinstance(value, dict):
            return
        if self._status != value:
            status_pkg = SimIOManager.build_status_pkg(value)
            if status_pkg is not None:
                self._status = status_pkg
                self._srv.status = status_pkg
            else:
                print 'problem with status value'
    status = property(get_status, set_status)

    def get_q_recv(self):
        return self._q_recv
    q_recv = property(get_q_recv)

    def get_q_send(self):
        return self._q_send
    q_send = property(get_q_send)

    def get_is_initialized(self):
        return self._is_initialized
    is_initialized = property(get_is_initialized)

    ## io methods

    def tick(self):
        """querys the receive-queue returns all queued items"""

        rval = []
        while not self._q_recv.empty():
            item = self._q_recv.get()
            rval.append(item)
            self._q_recv.task_done()
        return rval

    def send_package(
        self,
        tid=SimPkg.T_UKN,
        ident=SimPkg.NOIDENT,
        frame=SimPkg.NOFRAME,
        cont=None
    ):
        """build a package fromdata and send to clients

        SimPkg constructor wrapper

        :Parameters:
            pkg : SimPkg
                the SimPkg type id
                Default=T_UKN
            ident : long
                target ident or None if unrestricted
                Default=NOIDENT
            frame : long
                target frame
                Default=NOFRAME
            cont : list
                the conetnt of the package (ndarrays)
                Default=None
        """

        self.send_pkg(SimPkg(tid=tid, ident=ident, frame=frame, cont=cont))

    def send_pkg(self, pkg):
        """senda SimPkg to clients

        :Parameters:
            pkg : SimPkg
                The SimPkg instance to send.
        """

        self.q_send.put(pkg)

    ## static utility

    @staticmethod
    def build_status_pkg(status):
        """build a package from status

        items are mapped by id:
            0   : sample_rate
            1   : frame_size
            10  : neurons
            20  : recorders
        """

        try:
            cont = [
                [status['sample_rate'], 0],
                [status['frame_size'], 1]
            ]
            if len(status['neurons']) > 0:
                cont.extend([[item, 10] for item in status['neurons']])
            if len(status['recorders']) > 0:
                cont.extend([[item, 20] for item in status['recorders']])
            cont = N.asarray(cont, dtype=N.float32)
            return SimPkg(tid=SimPkg.T_STS, cont=cont)
        except:
            return None


##---MAIN

if __name__ == '__main__':

    from time import sleep

    print
    print 'setting up manager..'
    io_man = SimIOManager()
    io_man.initialize()

    try:
        while True:
            events = io_man.tick()
            while len(events) > 0:
                print events.pop(0)
            else:
                print '.'
            sleep(5)
            io_man.send_package(cont=N.ones((4, 4)))
    except KeyboardInterrupt:
        print
        print 'stoping due to KeyboardInterrupt'
        io_man.finalize()
