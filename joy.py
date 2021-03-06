from sys import stdout

import serial
import time

import struct

import subprocess

from interval import *


def calc_f(inter1: Interval, inter2: Interval, val):
    """
    i1.left i2.left i2.right i1.right
    -1   ...   0   ..   0   ...   1
    :param val: значение
    :param inter2: Границы нуля
    :param inter1: Границы от -1 до 1
    """
    if val < inter1.left:
        return -1
    if val < inter2.left:
        return - (val - inter2.left) / (inter1.left - inter2.left)
    if val < inter2.right:
        return 0
    if val < inter1.right:
        return (val - inter2.right) / (inter1.right - inter2.right)
    return 1


class Joy:
    """
    Протокол:
    xRaw 2b, yRaw 2b, xVal b, yVal b, actBtn 1b, secBtn 1b
    """

    _PROTOCOL_VERSION = b'b'

    _FMT = ">hhbbB"
    _center = None
    _center_x_interval = None
    _center_y_interval = None
    _x_interval = None
    _y_interval = None

    def __init__(self, file, speed=9600, config_file=None):
        self.config_file = config_file
        self.arduino = serial.Serial(file, speed)

        print("Wait...")
        time.sleep(4)

        readByte = self.arduino.read()
        if readByte != self._PROTOCOL_VERSION:
            raise RuntimeError("Joy Protocol error")

        try:
            self.load_config()
        except FileNotFoundError:
            self.calibrate()
        except ValueError:
            self.calibrate()

    def load_config(self):
        print("Load config...")
        if self.config_file is None:
            return

        f = open(self.config_file, "rt")

        self._center = [float(x) for x in f.readline().split(" ")]
        self._center_x_interval = Interval(*[float(x) for x in f.readline().split(" ")])
        self._center_y_interval = Interval(*[float(x) for x in f.readline().split(" ")])
        self._x_interval = Interval(*[float(x) for x in f.readline().split(" ")])
        self._y_interval = Interval(*[float(x) for x in f.readline().split(" ")])

        f.close()

    def save_config(self):
        if self.config_file is None:
            return

        f = open(self.config_file, "wt")

        lines = [
            " ".join([str(x) for x in self._center])
            , str(self._center_x_interval)
            , str(self._center_y_interval)
            , str(self._x_interval)
            , str(self._y_interval)]

        lines = [line + "\n" for line in lines]

        f.writelines(lines)

        f.close()

        print("Config save!")

    def calibrate(self, count=500):
        print("Need calibrate!")
        print("Drop old values...")

        self._center = [0, 0]
        xV, yV, *_ = self._get_raw_data()
        self._center_x_interval = Interval(xV, xV)
        self._center_y_interval = Interval(yV, yV)
        self._x_interval = Interval(xV, xV)
        self._y_interval = Interval(yV, yV)

        print("Put joy into center after 4 second...")
        time.sleep(4)

        for i in range(count):
            xV, yV, *_ = self._get_raw_data()
            self._center[0] += xV
            self._center[1] += yV

            self._center_x_interval.extend(xV)
            self._center_y_interval.extend(yV)
            if i % (count // 100) == 0:
                print('\r' + str(i * 100 // count), end="%")
                stdout.flush()

        self._center[0] /= count
        self._center[1] /= count

        self._center[0] = getRbyADC(self._center[0])
        self._center[1] = getRbyADC(self._center[1])
        self._center_y_interval.adc2r()
        self._center_x_interval.adc2r()

        print("Ok! X:({cxi.left}, {c[0]}, {cxi.right}); Y:({cyi.left}, {c[1]}, {cyi.right})".format(
            cxi=self._center_x_interval,
            cyi=self._center_y_interval,
            c=self._center
        ))

        print("Move your joy up-down-left-right after 4 sec...")
        time.sleep(4)
        for i in range(count):
            xV, yV, *_ = self._get_raw_data()
            self._x_interval.extend(xV)
            self._y_interval.extend(yV)
            if i % (count // 100) == 0:
                print('\r' + str(i * 100 // count), end="%")
                stdout.flush()

        self._x_interval.adc2r()
        self._y_interval.adc2r()

        print("Ok! X:[{xi.left}..{xi.right}]; [{yi.left}..{yi.right}]".format(
            xi=self._x_interval,
            yi=self._x_interval
        ))

        self.save_config()

    def _get_raw_data(self):
        self.arduino.write(struct.pack(">B", 15))
        self.arduino.flush()

        raw = self.arduino.read(struct.calcsize(self._FMT))

        data = struct.unpack(self._FMT, raw)
        return data

    def get_data(self):
        xV, yV, _, _, btn_mask = self._get_raw_data()

        xPos = calc_f(self._x_interval, self._center_x_interval, getRbyADC(xV))
        yPos = calc_f(self._y_interval, self._center_y_interval, getRbyADC(yV))

        return xPos, yPos, btn_mask


class JoyMouse(Joy):
    RELATIVE_MODE = 0
    ABSOLUTE_MODE = 1
    _BTN_ASSOC = {
        0: ("key", "Right"),
        1: ("key", "Up"),
        2: ("key", "Left"),
        3: ("key", "Down"),
        5: ("mouse", "3"),
        6: ("mouse", "1"),
    }

    def __init__(self, file, speed, config_file=None, mode=RELATIVE_MODE, screen_size=None):
        super(JoyMouse, self).__init__(file, speed, config_file=config_file)
        self.screen_size = screen_size or (1920, 1080)
        self.mode = mode
        self._old_btn_mask = 0

    def _mouse(self, xPos, yPos):
        param = "mousemove" + "_relative" if self.mode == self.RELATIVE_MODE else ""

        paramX = xPos
        paramY = yPos

        if self.mode == self.RELATIVE_MODE:
            paramX *= 10
            paramY *= 10

            paramX **= 3
            paramY **= 3

        paramX = int(paramX)
        paramY = int(paramY)
        if not (paramX == paramY == 0):
            return [param, "--", str(paramX), str(paramY)]
        else:
            return []

    def _keys(self, btnMask):
        actions = []

        for k, (tp, val) in self._BTN_ASSOC.items():
            newVal = btnMask >> k & 1
            if newVal != (self._old_btn_mask >> k & 1):
                actions += [tp + ("down" if newVal else "up"), val]

        self._old_btn_mask = btnMask

        return actions

    def step(self):
        xPos, yPos, btnMask = self.get_data()

        actions = self._mouse(xPos, yPos)

        actions += self._keys(btnMask)

        if actions:
            subprocess.call(["xdotool"] + actions)
            print(" ".join(actions))


