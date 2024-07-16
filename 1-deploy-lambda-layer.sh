#!/bin/bash
if ! hash aws 2>/dev/null || ! hash pip3 2>/dev/null; then
    echo "This script requires the AWS cli, and pip3 installed"
    exit 2
fi

#S3_BUCKET_NAME=examplelabs.net
#CF_STACK_NAME=lambda-access-eks-stack
LAYER_NAME=python-k8s-layer
PYTHON_VERSION=python3.9

# 创建 Lambda Layer
python3 -m venv create_layer
source create_layer/bin/activate
pip3 install -r requirements.txt

mkdir python
cp -r create_layer/lib python/
zip -r layer_content.zip python

aws lambda publish-layer-version --layer-name $LAYER_NAME \
    --zip-file fileb://layer_content.zip \
    --compatible-runtimes $PYTHON_VERSION \
    --compatible-architectures "x86_64"

zip -r code.zip lambda_function.py
aws s3 cp code.zip s3://$S3_BUCKET_NAME

# 获取 Lambda Layer 版本 ARN
aws lambda list-layer-versions --layer-name $LAYER_NAME --query 'LayerVersions[0].LayerVersionArn' --output text
