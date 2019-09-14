amazon-linux-extras install -y \
    docker \
    emacs
    python3

systemctl start docker
yum install git

git clone https://github.com/B0001/bible.git

python3 -m pip install --upgrade --user pip
python3 -m pip install --upgrade --user boto3
