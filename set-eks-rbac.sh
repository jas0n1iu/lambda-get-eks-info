#!/bin/bash
if ! hash aws 2>/dev/null || ! hash kubectl 2>/dev/null || ! hash eksctl 2>/dev/null; then
    echo "This script requires the AWS cli, kubectl, and eksctl installed"
    exit 2
fi

set -eo pipefail

ROLE_ARN='arn:aws:iam::373127939256:role/lambda_eks-role-cnhbrqp5'
RBAC_OBJECT='kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: read-only3
  namespace: kube-system
rules:
- apiGroups: [""]
  resources: ["*"]
  verbs: ["get", "watch", "list"]
---
kind: RoleBinding
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: read-only-binding3
  namespace: kube-system
roleRef:
  kind: Role
  name: read-only3
  apiGroup: rbac.authorization.k8s.io
subjects:
- kind: Group
  name: read-only-group3'

# 获取所有 EKS 集群名称
CLUSTER_NAMES=$(eksctl get cluster --output json | jq -r '.[].Name')

# 获取所有可用的上下文
CONTEXTS=$(kubectl config get-contexts -o name)

for CLUSTER_NAME in $CLUSTER_NAMES; do
    echo "==========================================================
Cluster: $CLUSTER_NAME"

    # 使用正则表达式查找与集群名称完全匹配的上下文
    CONTEXT=$(echo "$CONTEXTS" | grep -E "^[^@]+@$CLUSTER_NAME\.[^@]+$")

    # 如果找到匹配的上下文
    if [ -n "$CONTEXT" ]; then
        # 切换到当前集群的 Kubernetes 上下文
        kubectl config use-context "$CONTEXT"

        echo "==========
Create Role and RoleBinding in Kubernetes with kubectl"
        echo "$RBAC_OBJECT"
        echo
        while true; do
            read -p "Do you want to create the Role and RoleBinding? (y/n)" response
            case $response in
                [Yy]* ) echo "$RBAC_OBJECT" | kubectl apply -f -; break;;
                [Nn]* ) break;;
                * ) echo "Response must start with y or n.";;
            esac
        done

        echo
        echo "==========
Update aws-auth configmap with a new mapping"
        echo "RoleArn: $ROLE_ARN"
        echo
        while true; do
            read -p "Do you want to create the aws-auth configmap entry? (y/n)" response
            case $response in
                [Yy]* ) eksctl create iamidentitymapping --cluster $CLUSTER_NAME --group read-only-group --arn $ROLE_ARN; break;;
                [Nn]* ) break;;
                * ) echo "Response must start with y or n.";;
            esac
        done
    else
        echo "No matching context found for cluster $CLUSTER_NAME"
    fi
done