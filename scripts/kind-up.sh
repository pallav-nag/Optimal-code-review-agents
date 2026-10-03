#!/usr/bin/env bash
# Local Kubernetes: kind + Strimzi (Kafka operator) + KEDA + GraphReview dev overlay.
set -euo pipefail
cd "$(dirname "$0")/.."
NS=graphreview

for bin in kind kubectl helm docker; do
  command -v "$bin" >/dev/null || { echo "missing: $bin"; exit 1; }
done

kind get clusters | grep -qx graphreview || kind create cluster --config deploy/kind/cluster.yaml
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -

echo "▶ Strimzi operator (watching $NS)"
kubectl apply --server-side -n "$NS" -f "https://strimzi.io/install/latest?namespace=$NS"
kubectl wait --for=condition=Established crd/kafkas.kafka.strimzi.io crd/kafkanodepools.kafka.strimzi.io \
  crd/kafkatopics.kafka.strimzi.io --timeout=120s
kubectl -n "$NS" rollout status deploy/strimzi-cluster-operator --timeout=300s

echo "▶ KEDA"
helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
helm repo update kedacore >/dev/null
helm upgrade --install keda kedacore/keda -n keda --create-namespace --wait

echo "▶ metrics-server (CPU-based HPAs for gateway/context)"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml >/dev/null
kubectl -n kube-system patch deploy metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' >/dev/null 2>&1 || true

echo "▶ images"
docker build --target core -t graphreview-core:dev .
docker build --target rag -t graphreview-rag:dev .
kind load docker-image graphreview-core:dev graphreview-rag:dev --name graphreview

echo "▶ app"
[[ -f deploy/k8s/overlays/dev/secrets.env ]] || cp deploy/k8s/overlays/dev/secrets.env.example deploy/k8s/overlays/dev/secrets.env
kubectl apply -k deploy/k8s/overlays/dev
kubectl -n "$NS" wait kafka/graphreview --for=condition=Ready --timeout=600s
kubectl -n "$NS" rollout status deploy/gateway --timeout=300s
kubectl -n "$NS" get pods,scaledobjects
echo "✓ gateway on http://localhost:8080  (edit deploy/k8s/overlays/dev/secrets.env, then re-apply)"
