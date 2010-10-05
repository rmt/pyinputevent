#!/usr/bin/python

from uinput import UInputDevice
from pyinputevent import InputEvent, SimpleDevice
from keytrans import *
import select
import scancodes as S
import logging
import getopt
import sys
import fcntl

def detect_hama_mce():
    fd = file("/proc/bus/input/devices")
    entry = {}
    mouse = None
    kbd = None
    for line in fd:
        line = line.strip()
        if not line and entry:
            if entry['N'] == 'Name="HID 05a4:9881"':
                ev = None
                ismouse = False
                for h in entry['H'].split(' '):
                    if 'mouse' in h:
                        ismouse = True
                    if 'event' in h:
                        ev = h
                if ismouse:
                    mouse = ev
                else:
                    kbd = ev
        elif line:
            l, r = line.split(":", 1)
            r = r.strip()
            entry[l] = r
    return mouse, kbd


#
INP_SYNC = InputEvent.new(0, 0, 0)

class ForwardDevice(SimpleDevice):
    def __init__(self, udev, *args, **kwargs):
        SimpleDevice.__init__(self, *args, **kwargs)
        self.udev = udev # output device
        self.ctrl = False
        self.alt = False
        self.shift = False
        self.state = None
        self.doq = False # queue keystrokes for processing?
        self.mouseev = []
        self.keyev = []
        self.parser = KeymapParser("keymap.txt")

    def send_all(self, events):
        for event in events:
            logging.debug(" --> %r" % event)
            self.udev.send_event(event)

    @property
    def modcode(self):
        code = 0
        if self.shift:
            code += 1
        if self.ctrl:
            code += 2
        if self.alt:
            code += 4
        return code
    def receive(self, event):
        logging.debug("<--  %r" % event)
        if event.etype == S.EV_MSC:
            return
        elif event.etype == S.EV_REL or event.etype == S.EV_ABS:
            self.mouseev.append(event)
            return
        elif event.etype == S.EV_KEY:
            if event.ecode in (S.KEY_LEFTCTRL, S.KEY_RIGHTCTRL):
                self.ctrl = bool(event.evalue)
                return
            elif event.ecode in (S.KEY_LEFTALT, S.KEY_RIGHTALT):
                self.alt = bool(event.evalue)
                return
            elif event.ecode in (S.KEY_LEFTSHIFT, S.KEY_RIGHTSHIFT):
                self.shift = bool(event.evalue)
                return
            else:
                self.send_all(self.parser.process(KeyEvent(event, self.modcode)))
        elif event.etype == 0:
            if self.mouseev:
                self.send_all(self.mouseev + [ INP_SYNC ])
                self.mouseev = []
            #print "-------------- sync --------------"
            return
        else:
            print "Unhandled event: %r" % event
            #self.udev.send_event(event)


def main(devs):
    udev = UInputDevice("Virtual Input Device", 0x0, 0x1, 1)
    udev.create()
    poll = select.poll()
    fds = {}
    for devpath in devs:
        dev = ForwardDevice(udev, devpath, devpath)
        poll.register(dev, select.POLLIN | select.POLLPRI)
	fcntl.ioctl(dev.fileno(), 0x40044590, 1)
        fds[dev.fileno()] = dev
    while True:
        for x,e in poll.poll():
            dev = fds[x]
            dev.read()

if __name__ == '__main__':
    logger = logging.getLogger()
    for arg in getopt.getopt(sys.argv, "vq")[1]:
        if arg == "-v":
            logger.setLevel(logger.getEffectiveLevel()-10)
        elif arg == "-q":
            logger.setLevel(logger.getEffectiveLevel()+10)
    mousedev, kbddev = detect_hama_mce()
    if not mousedev or not kbddev:
        logging.error("HAMA MCE Remote not detected")
        sys.exit(1)
    devs = [
        ("/dev/input/%s" % mousedev),
        ("/dev/input/%s" % kbddev),
    ]
    logging.info("Listening on %r" % devs)
    main(devs)
