#!/bin/bash
# create docker volume to hold data for gwasdiversitymonitor

docker volume create gwas_data
docker run -v gwas_data:/vol --name helper busybox true
docker cp ./data helper:/vol
docker rm helper

#docker run -it --rm -v gwas_data:/vol busybox ls -l /vol/data
#docker volume rm gwas_data

docker run -it --rm -v gwasdiversitymonitor_data:/vol busybox ls -l /vol/data
docker exec -it gwas_flask /bin/bash