#!/bin/bash

python3 /root/scripter.py
rc=$?
chmod -R 777 /output
exit $rc
