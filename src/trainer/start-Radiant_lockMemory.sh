#!/bin/bash

while true
do
sudo ./pt_main --gpu-memory-clock 810 --algo sha512256d --url stratum+tcp://stratum-eu.rplant.xyz:7086 --user PoolDonate --pass x
sleep 5
done
