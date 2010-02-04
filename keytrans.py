#
# Translate input key/button events to output events using a config file.
#

__all__ = (
    'MOD_SHIFT', 'MOD_CTRL', 'MOD_ALT',
    'revmap', 'KeyEvent', 'KeymapParser',
)

from pyinputevent import InputEvent
import scancodes
import logging
import time
import compiler
S = scancodes

#
MOD_SHIFT = 1
MOD_CTRL = 2
MOD_ALT = 4
#

revmap = {}
for k,v in scancodes.__dict__.items():
    if k.startswith("KEY_") or k.startswith("BTN_"):
        revmap[v] = k

class KeyEvent(object):
    def __init__(self, event, modstate):
        self.event = event
        self.modstate = modstate
    @property
    def keydown(self):
        return self.event.evalue == 1
    @property
    def keyup(self):
        return self.event.evalue == 0
    def to_input_events(self):
        """
        Return a sequence of InputEvents for this action.
        If it's a keydown event and there are modifiers, press them before
        the event, and release them afterwards.  If it's a keyup, just
        return the single event.
        """
        res = []
        if self.keydown:
            if self.modstate & MOD_CTRL:
                res += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTCTRL,  1) ]
            if self.modstate & MOD_ALT:
                res += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTALT,   1) ]
            if self.modstate & MOD_SHIFT:
                res += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTSHIFT, 1) ]
            res += [ self.event ]
            if self.modstate & MOD_SHIFT:
                res += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTSHIFT, 0) ]
            if self.modstate & MOD_ALT:
                res += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTALT,   0) ]
            if self.modstate & MOD_CTRL:
                res += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTCTRL,  0) ]
        else:
            res += [ self.event ]
        res += [ InputEvent.new(0, 0, 0) ]
        return res
    def __str__(self):
        mod = ""
        if self.modstate & MOD_CTRL:
            mod += "Ctrl-"
        if self.modstate & MOD_ALT:
            mod += "Alt-"
        if self.modstate & MOD_SHIFT:
            mod += "Shift-"
        mod += revmap.get(self.event.ecode, str(self.event.ecode))
        if self.keydown:
            mod += "-down"
        else:
            mod += "-up"
        return mod
    def __repr__(self):
        return "<KeyEvent %s>" % str(self)

def make_keyevents(keystring):
    res = []
    ks = keystring.split(" ")
    untap = []
    for k in ks:
        l = None
        while True:
            if k.startswith("Alt-"):
                k = k[4:]
                res += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTALT, 1) ]
                untap += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTALT, 0) ]
            elif k.startswith("Ctrl-"):
                k = k[5:]
                res += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTCTRL, 1) ]
                untap += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTCTRL, 0) ]
            elif k.startswith("Shift-"):
                k = k[6:]
                res += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTSHIFT, 1) ]
                untap += [ InputEvent.new(S.EV_KEY, S.KEY_LEFTSHIFT, 0) ]
            else:
                break
        if "-" in k:
            k, l = k.split("-", 1)
        if hasattr(scancodes, k):
            sc = getattr(scancodes, k)
            if l is None or l == "down":
                res += [ InputEvent.new(S.EV_KEY, sc, 1) ]
            if l is None or l == "up":
                res += [ InputEvent.new(S.EV_KEY, sc, 0) ]
        else:
            logging.warning("Unknown key %s" % k)
        if untap:
            untap.reverse()
            res += untap
        if res:
            res.append(InputEvent.new(0, 0, 0))
    return res

class KeymapParser(object):
    def __init__(self, configfd):
        if isinstance(configfd, basestring):
            configfd = file(configfd, "r")
        self.map = {}
        self.queue = []
        self.vars = {}
        for line in configfd:
            line = line.strip()
            if line.startswith('#') or not line:
                continue
            left, right = line.split("=", 1)
            left = left.strip()
            right = map(str.strip, right.split(";"))
            self.map[left] = right
    def process(self, keyevent):
        logging.debug("Received %s" % keyevent)
        if self.queue:
            s = " ".join([str(e) for e in self.queue]) + " " + str(keyevent)
        else:
            s = str(keyevent)
        if s in self.map:
            self.vars['queue'] = self.queue
            self.vars['keyevent'] = keyevent
            actions = self.map[s]
            res = []
            state = True
            for action in actions:
                logging.debug("Processing action: %s" % action)
                if action.startswith("if "):
                    state = bool(eval(action[3:], globals(), self.vars))
                    logging.debug("if expression evaluated to %s" % state)
                    continue
                elif action.startswith("else"):
                    state = not state
                    continue
                if not state:
                    logging.debug("skipping action because of if or else: %s" % action)
                    continue
                if action.startswith("send "):
                    res += make_keyevents(action[5:])
                elif action == "forward":
                    for x in self.queue:
                        res += x.to_input_events()
                    res += keyevent.to_input_events()
                elif action == "wait":
                    self.queue.append(keyevent)
                elif action == "clear":
                    self.queue = []
                elif action == "none":
                    continue
                elif action.startswith("echo "):
                    logging.info(action[5:])
                    continue
                elif action.startswith("exec "):
                    self.vars['res'] = None
                    code = compiler.compile(action[5:], "action", "single")
                    eval(code, globals(), self.vars)
                elif action.startswith("set "):
                    k,v = action[4:].split(" ",1)
                    try:
                        self.vars[k] = eval(v.strip(), globals(), self.vars)
                    except:
                        logging.exception("Exception while processing action: %s" % action)
                        return []
                elif action.startswith("call "): # call a python function
                    # format: module:function:arg
                    modname = None
                    funcname = None
                    arg = None
                    tmp = action[6:].split(":", 2)
                    if len(tmp) == 3:
                        modname, funcname, arg = tmp
                    elif len(tmp) == 2:
                        modname, funcname = tmp
                    if modname and funcname:
                        try:
                            mod = __import__(modname)
                            func = getattr(mod, funcname)
                            return list(func(self.queue + [keyevent], self.vars, arg))
                        except:
                            logging.exception("Exception while processing action: %s" % action)
                            return []
                else:
                    logging.warn("Unknown action: %s" % action)
            logging.debug("KeymapParser:process returning %r" % res)
            return res
        logging.warn("Unknown key sequence: %s" % s)
        return []

def test():
    logging.getLogger().setLevel(logging.INFO)
    ts = "KEY_LEFTCTRL-down KEY_P KEY_LEFTCTRL-up"
    logging.info("%s %r" % (ts, make_keyevents(ts)))
    from StringIO import StringIO
    s = StringIO("""
Ctrl-KEY_P-down = wait
Ctrl-KEY_P-down KEY_P-up = echo send Shift-KEY_P; send Shift-KEY_P; clear
Ctrl-Alt-KEY_P-down = wait
Ctrl-Alt-KEY_P-down KEY_P-up = forward; clear
BTN_RIGHT-down = set rightclick time.time()
BTN_RIGHT-up = if (time.time()-rightclick) > 0.3; send BTN_RIGHT
""")
    parser = KeymapParser(s)
    res = parser.process(KeyEvent(InputEvent.new(S.EV_KEY, S.KEY_P, 1), MOD_CTRL))
    assert len(res) == 0
    res = parser.process(KeyEvent(InputEvent.new(S.EV_KEY, S.KEY_P, 0), 0))
    logging.info(res)
    res = parser.process(KeyEvent(InputEvent.new(S.EV_KEY, S.BTN_RIGHT, 1), 0))
    assert len(res) == 0
    time.sleep(0.35)
    res = parser.process(KeyEvent(InputEvent.new(S.EV_KEY, S.BTN_RIGHT, 0), 0))
    logging.info(res)
    res = parser.process(KeyEvent(InputEvent.new(S.EV_KEY, S.KEY_P, 1), MOD_CTRL | MOD_ALT))
    assert len(res) == 0
    logging.info("Should be control down, alt down, P down, alt up, control up, sync, P up")
    res = parser.process(KeyEvent(InputEvent.new(S.EV_KEY, S.KEY_P, 0), 0))
    logging.info(res)

if __name__ == '__main__':
    test()
