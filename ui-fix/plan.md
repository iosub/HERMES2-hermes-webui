# Instructions

- Always use English for comments and code.
- Touch only the minimum code required to implement the new functionality.
- This folder and this document exist only for this branch.
- Do not include this folder in the final PR to `main`.
- allways activate el venv with uv

# quiero ajustar las dimensiones de la interfase para que se vea toda las pantalla 
# vamos a probar el diseño usando browse simple
ahora pon que 

# Crear branch de pruebas desde main

```bash
cd /root/.hermes/hermes-agent

# Asegurarse de estar en main y actualizado
git checkout main
hermes update        # actualiza al último main de NousResearch (opcional)

# Crear el branch de prueba
git checkout -b t12

# Publicarlo en tu fork (remote "virtud" = iosub/IA-HERMES-VIRTUD)
git push -u virtud t12
```
# actu IA-HERMES-MIWORKSPACE
cd /root/.hermes
git checkout -b t12
git push -u origin t12
git add -A
git commit -m "hermes update: skills actualizadas + doc ComoHacerPruebas"
git push origin t12

# commit cambios
git add -A
git commit -m "Lmejj"
git push origin v12

Para volver a main cuando termines:

```bash
git checkout main
```
Unaitxo@13

fuser -k 5000/tcpst 
./start.sh 