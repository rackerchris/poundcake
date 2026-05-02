# Shared Storage

This directory contains optional cluster-scoped storage manifests and examples used by PoundCake.

## Longhorn RWX

Longhorn supports `ReadWriteMany` volumes through share-manager pods backed by NFSv4.

Included manifest:

- `longhorn-rwx-storageclass.yaml`: example StorageClass for shared `ReadWriteMany` PVCs.

You can apply it directly:

```bash
kubectl apply -f config/storage/longhorn-rwx-storageclass.yaml
```

Or let the Helm chart create the same class by enabling:

```yaml
longhorn:
  rwxStorageClass:
    create: true
    name: longhorn-rwx
```
