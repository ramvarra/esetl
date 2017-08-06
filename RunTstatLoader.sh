#!/bin/bash

cd /mnt/homefiler/esetl
. ESETL_ENV.sh

SLEEP_SECS=300

while true
do
	/usr/local/anaconda3/bin/python TStatLoader.py
	sleep $SLEEP_SECS
done
