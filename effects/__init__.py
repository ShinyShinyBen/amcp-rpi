"""Light effects for the Ardent Mobile Cloud Platform"""
#
# Copyright (c) 2013 Ardent Heavy Industries, LLC.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# 

import time
import sys
import numpy
import json
import math

import cloud
import fastopc


class Model(object):
    """A model of the physical sculpture. Holds information about the position of the LEDs.

       In the animation code, LEDs are represented as zero-based indices which match the
       indices used by the OPC server.
       """

    def __init__(self, filename):
        # Raw graph data
        self.graphData = json.load(open(filename))

        # Points, as a NumPy array
        self.points = numpy.array([x['point'] for x in self.graphData])

        # Packed buffer, ready to pass to our native code
        self.packed = self.points.astype(numpy.float32).tostring()


class LightParameters(object):
    """Container for parameters that are intended to be tweaked by the performer, via OSC."""

    # How much detail is visible (scale factor)
    detail = 0.8

    # How fast the cloud shape changes over time. 0 == perfectly still.
    turbulence = 0.4

    # Wind heading, in degrees, and wind speed in meters per second.
    wind_heading = 0
    wind_speed = 0.8

    # Z-axis rotation angle for the whole cloud, in degrees
    rotation = 0

    # Color temperature, in Kelvin
    temperature = 6000

    # Brightness and contrast for the cloud effect itself. Contrast is given
    # as a proportion of the total effect brightness.
    brightness = 0.3
    contrast = 0.9


def temperatureToRGB(kelvin):
    """Approximation for RGB value given a color temperature in Kelvin.
       Returns 3-element NumPy array of floats in the range [0,1].

       This uses the non-table-driven approximation from:
       http://www.tannerhelland.com/4435/convert-temperature-rgb-algorithm-code/
       """

    t = kelvin / 100.0

    if t <= 66:
        r = 1.0
        g = 0.3900815787690196 * math.log(t) - 0.6318414437886275
        if t <= 19:
            b = 0.0
        else:
            b = 0.543206789110196 * math.log(t - 10) - 1.19625408914
    else:
        r = 1.292936186062745 * math.pow(t - 60, -0.1332047592)
        g = 1.129890860895294 * math.pow(t - 60, -0.0755148492)
        b = 1.0

    return numpy.clip([r,g,b], 0.0, 1.0)


class LightController(object):
    """Light effect controller. Stores effect parameters, and runs our main loop which
       calculates pixel values and streams them to the Open Pixel Control server.
       """

    def __init__(self, layout="layout/amcp-leds.json", server=None, targetFPS=59.9):
        self.model = Model(layout)
        self.opc = fastopc.FastOPC(server)
        self.targetFPS = targetFPS
        self.time = time.time()

        # Current translation vector
        self.translation = [0,0,0,0]

        # Parameters, intended to be modified by the server code
        self.params = LightParameters()

        # Array with state of DMX devices. (TODO)
        self.dmx = numpy.zeros((1, 3), numpy.uint8)

        self._fpsFrames = 0
        self._fpsTime = 0
        self._fpsLogPeriod = 0.5    # How often to log frame rate

    def run(self):
        """Main rendering loop. Calls runFrame(), keeps track of frames per second."""

        while True:
            self._drawFrame(self._advanceTime())

    def _advanceTime(self):
        """Update our virtual clock (self.time)
           Returns the time delta (dt)

           This is where we enforce our target frame rate, by sleeping until the minimum amount
           of time has elapsed since the previous frame. We try to synchronize our actual frame
           rate with the target frame rate in a slightly loose way which allows some jitter in
           our clock, but which keeps the frame rate centered around our ideal rate if we can keep up.

           This is also where we log the actual frame rate to the console periodically, so we can
           tell how well we're doing.
           """

        now = time.time()
        dt = now - self.time
        dtIdeal = 1.0 / self.targetFPS

        if dt > dtIdeal * 2:
            # Big jump forward. This may mean we're just starting out, or maybe our animation is
            # skipping badly. Jump immediately to the current time and don't look back.

            self.time = now

        else:
            # We're approximately keeping up with our ideal frame rate. Advance our animation
            # clock by the ideal amount, and insert delays where necessary so we line up the
            # animation clock with the real-time clock.

            self.time += dtIdeal
            if dt < dtIdeal:
                time.sleep(dtIdeal - dt)

        # Log frame rate

        self._fpsFrames += 1
        if now > self._fpsTime + self._fpsLogPeriod:
            fps = self._fpsFrames / (now - self._fpsTime)
            self._fpsTime = now
            self._fpsFrames = 0
            sys.stderr.write("%7.2f FPS\n" % fps)

        return dt

    def _updateTranslation(self, dt):
        # Update translations according to delta-T.
        # If our heading changes, that only applies to current and future
        # wind motion. Translation changes must take into account zoom,
        # since translations are in noise-space rather than model-space.

        dtz = dt * self.params.detail
        a = math.radians(self.params.wind_heading)
        self.translation[0] += math.cos(a) * self.params.wind_speed * dtz
        self.translation[1] += math.sin(a) * self.params.wind_speed * dtz
        self.translation[3] += self.params.turbulence * dtz

    def _makeCloudMatrix(self):
        # Assemble a matrix with Z-axis euler rotation, scaling by our 'detail'
        # factor, and translation by our stored 4-vector.
        # 
        # The noise field wraps at 1024 units, so we take
        # the translation to be modulo-1024 to prevent problems when our
        # potentially-large translations are cast to single-precision float
        # in our native code module.

        z = self.params.detail
        t = numpy.fmod(self.translation, 1024.0)
        a = math.radians(self.params.rotation)
        s = z * math.sin(a)
        c = z * math.cos(a)

        return [ c,      -s,    0,    0,
                 s,       c,    0,    0,
                 0,       0,    z,    0,
                 t[0], t[1], t[2], t[3] ]

    def _makeLightning(self, dt):
        # Calculate lightning parameters for this frame
        # TODO

        return []

    def _drawFrame(self, dt):
        self._updateTranslation(dt)
        matrix = self._makeCloudMatrix()
        lightning = self._makeLightning(dt)

        # Calculate the white point for our cloud effect, based on the given color temperature,
        # and calculate other colors based on brightness and contrast settings.
        white = temperatureToRGB(self.params.temperature)
        baseColor = white * self.params.brightness
        noiseColor = baseColor * self.params.contrast

        # Calculate our main cloud effect (Native code)
        cloudPixels = cloud.render(self.model.packed, matrix, baseColor, noiseColor, lightning)

        # Pack together our raw cloud pixels and NumPy DMX array, send it all off over OPC.
        self.opc.putPixels(0, cloudPixels, self.dmx)
