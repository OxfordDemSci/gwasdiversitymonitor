#!/bin/bash
sudo apt-get update -y
sudo apt-get upgrade -y

#---- docker ----#

# install docker
sudo apt-get install ca-certificates curl gnupg lsb-release

sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo apt-get update && sudo apt-get install -y docker-compose

# add user to docker group
sudo usermod -aG docker ubuntu

# crontab to persist read/write access to docker socket
sudo crontab -e
# @reboot chmod a+rw /var/run/docker.sock


#---- github ----#
sudo apt install git

# create GitHub deploy key
cd ~/.ssh
ssh-keygen -t rsa -N '' -f ~/.ssh/id_rsa <<<n >/dev/null 2>&1

#!! Remember to authorize the deploy key on GitHub

# ssh config for github authentication
echo "Host github" >> ~/.ssh/config
echo -e "\tHostName ssh.github.com" >> ~/.ssh/config
echo -e "\tUser git" >> ~/.ssh/config
echo -e "\tIdentityFile ~/.ssh/id_rsa" >> ~/.ssh/config

# clone repository
cd ~
git clone github:OxfordDemSci/gwasdiversitymonitor

# ---- cron ---- #
cd ~/gwasdiversitymonitor
sudo cp deploy/gwasdiversitymonitor_crontab /etc/cron.d/

# ---- launch gwasdiversitymonitor ---- #
cd ~/gwasdiversitymonitor
docker-compose up -d
