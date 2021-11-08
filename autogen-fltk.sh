#!/bin/bash

for f in ../cfltk/include/*
do
    python autogen.py -i "$f" -o "../quickjs-fltk/$(basename -s ".h" $f).js"
    # sleep 2
done
