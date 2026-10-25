# Evidencia de capturas

Cada carpeta de `reports/` conserva la evidencia reproducible de una captura
(metadatos, hashes, snapshots comprimidos y scripts `verify.py`). Los archivos
crudos grandes (tar.gz con todas las respuestas HTTP y snapshots de más de 1 MB)
**no viven en el repositorio**: están en el release que indica
[`evidencia-archivada.json`](evidencia-archivada.json), con su tamaño y SHA-256.

Para correr las pruebas que los usan:

```bash
PRECIOS_SPS_EVIDENCIA=descargar pytest precios-supermercados-sps/tests
```

`tests/_evidencia.py` descarga cada archivo a su ruta original, comprueba el
SHA-256 y lo deja ahí (ignorado por git). Sin esa variable, esos módulos se omiten
con un aviso. Para archivar un lote nuevo: agregar los archivos al índice, correr
el workflow "Precios SPS - Archivar evidencia de capturas" y después quitarlos del
árbol.
