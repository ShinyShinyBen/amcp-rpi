#!/bin/sh
#
# Install system configuration and pre-built binaries to
# a Raspbian system.
#
# the intention is for the setup procedure to be:
#   1) Install Raspbian
#   2) Clone git@github.com:ArdentHeavyIndustries/amcp-rpi.git into /home/pi/amcp-rpi.git
#   3) CD to this directory
#   4) Run ./install.sh
#   5) Reboot

sudo apt-get install python-avahi python-dev supervisor avahi-daemon python-liblo libao4 libev4 autoconf libudev-dev libev-dev mpg123 python-pygame

python ../setup.py build --build-platlib=.

sudo cp supervisor/* /etc/supervisor/conf.d/

sudo cp -r etc usr lib /
sudo update-rc.d fcserver defaults
sudo update-rc.d shairport defaults

pushd ../media
for f in *.mp3; do 
    mp123 -w ${f%%mp3}wav $f
done;
popd

cd ~

git clone https://github.com/ArdentHeavyIndustries/amcp-osc.git

