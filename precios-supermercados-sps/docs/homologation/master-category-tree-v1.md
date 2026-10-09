# Árbol maestro de categorías v1

Aprobado por el responsable del proyecto el 2026-10-08. Una sola clasificación pública para los seis supermercados, con cuatro niveles: **Departamento > Categoría > Subcategoría > Tipo de producto**, siguiendo la estructura de GS1 GPC (Segmento > Familia > Clase > Bloque).

## Archivos versionados

- `config/homologation/master-category-tree-v1.json`: el árbol (14 departamentos, 180 subcategorías) con el segmento GPC de referencia por departamento y los tipos de producto (nivel 4) que ya produce el motor de homologación.
- `config/homologation/source-category-crosswalk-v1.csv`: tabla de equivalencias. Una fila por categoría publicada por cada supermercado (1,311 filas capturadas de la corrida diaria 37796223788 del 2026-10-08, más 18 que avisó la primera publicación: 1,329). Columnas: `supermarket_id, source_category, department, category, subcategory, level`. `level` = `subcategory` (1246), `category` (57), `department` (17), `by_name` (8: la categoría del súper mezcla cosas distintas) o `excluded` (1: fuera del catálogo, p. ej. tarjetas de regalo). La comparación de `source_category` ignora mayúsculas, acentos y espacios.
- `src/precios_supermercados/master_taxonomy.py`: carga y valida ambos archivos y asigna el nodo público.

## Orden de asignación

1. Decisión manual (reservado; aún sin decisiones registradas).
2. Mismo producto en otro supermercado: un grupo comparable comparte el nodo más específico y más votado de sus ofertas.
3. Tabla de equivalencias de la categoría que publica el súper.
4. Tipo de producto por nombre (motor de homologación), sólo si cae dentro de la rama que da la equivalencia; si la contradice, gana la equivalencia.
5. Sin evidencia: sin categoría (lista de revisión). Como último recurso se traduce el departamento de la taxonomía interna previa (p. ej. `Bebidas` → `Bebidas y tabaco`). Nunca se inventa una categoría.

## Publicación (catálogo B2C v3)

- `category` = **Departamento** del árbol.
- `product_type` = segundo nivel navegable: el tipo de producto si existe; si no, la subcategoría; si no, la categoría del árbol.
- Las filas `excluded` no se publican.
- La taxonomía interna del motor de homologación (guardas de identidad, tipo alimenticio/no alimenticio) **no cambia**: el árbol sólo decide la clasificación pública. El Business Mart B2B conserva por ahora la taxonomía interna.

## Gobierno

- Un producto, un nodo, al nivel más bajo posible.
- Un nodo nuevo, un cambio de nombre o una fusión entra sólo con aprobación del responsable, con fecha y nueva versión del JSON.
- Categoría nueva del súper: el exportador imprime `taxonomy_unmapped_source_categories` en el log de publicación; se agrega una fila al CSV (no bloquea la publicación; esos productos usan el tipo por nombre o quedan sin categoría).
- Temporada como atributo: Navidad o Halloween no son departamentos; el producto va a lo que es.
- Metas mensuales: ≥ 98 % de productos con categoría, ≥ 95 % de aciertos en una muestra de 100 revisada a mano y 0 categorías del súper sin mapear.

## Departamentos

| Código | Departamento | Categorías | Subcategorías | Segmento GPC de referencia |
| --- | --- | ---: | ---: | --- |
| 01 | Alimentos | 7 | 39 | Food/Beverage/Tobacco |
| 02 | Bebidas y tabaco | 4 | 15 | Food/Beverage/Tobacco |
| 03 | Cuidado personal y belleza | 8 | 24 | Beauty/Personal Care/Hygiene |
| 04 | Salud y farmacia | 5 | 15 | Healthcare |
| 05 | Bebé | 4 | 7 | Baby Care |
| 06 | Limpieza | 4 | 16 | Cleaning/Hygiene Products |
| 07 | Mascotas | 4 | 7 | Pet Care/Food |
| 08 | Hogar y cocina | 7 | 14 | Kitchen Merchandise / Household Furniture/Furnishings |
| 09 | Ferretería y autos | 2 | 10 | Tools/Equipment / Automotive |
| 10 | Electrónica y electrodomésticos | 2 | 8 | Audio Visual / Computing / Communications / Home Appliances |
| 11 | Ropa, calzado y accesorios | 3 | 7 | Clothing / Footwear / Personal Accessories |
| 12 | Juguetes | 1 | 9 | Toys/Games |
| 13 | Deportes y aire libre | 2 | 5 | Sports Equipment / Camping |
| 14 | Papelería, oficina y fiestas | 3 | 4 | Stationery/Office Machinery/Occasion Supplies |

## Árbol completo

### 01 Alimentos

- **01.01 Abarrotes:** Aceites y grasas (Aceite comestible, Margarina), Arroz, granos y legumbres (Arroz, Frijol), Azúcar y endulzantes (Azúcar), Harinas y repostería (Harina de maíz, Harina de trigo), Pastas (Pasta), Sopas y comidas instantáneas (Sopa), Enlatados y conservas (Atún, Sardina), Salsas, aderezos y vinagres (Aderezo, Ketchup, Mayonesa, Mostaza, Pasta de tomate, Salsa), Especias y condimentos (Sal), Untables, mermeladas y miel (Mantequilla de maní, Miel), Cereales, avenas y barras (Avena, Cereal), Productos vegetales y especialidades
- **01.02 Snacks y dulces:** Papas, frituras y boquitas, Frutos secos y fruta deshidratada, Galletas (Galleta), Dulces y chocolates (Chocolate), Gelatinas, flanes y postres (Gelatina)
- **01.03 Lácteos y huevos:** Leche (Leche, Leche condensada, Leche en polvo, Leche evaporada), Quesos (Queso), Yogurt (Yogurt), Mantequillas, margarinas y cremas (Mantequilla), Huevos (Huevo)
- **01.04 Carnes, aves y mariscos:** Res, Cerdo, Pollo y pavo, Pescados y mariscos, Embutidos y carnes frías, Proteínas vegetales
- **01.05 Frutas y verduras:** Frutas, Verduras, Hierbas y montes
- **01.06 Panadería y tortillas:** Pan salado y de molde (Pan, Pan de molde), Pan dulce, pasteles y repostería, Tortillas
- **01.07 Congelados y comidas preparadas:** Comidas listas y congeladas, Comida preparada y deli, Helados y postres congelados (Helado), Frutas y verduras congeladas, Hielo

### 02 Bebidas y tabaco

- **02.01 Bebidas sin alcohol:** Agua (Agua), Gaseosas (Refresco), Jugos y néctares (Jugo), Energizantes e hidratantes, Café y té listos para beber, Bebidas en polvo y concentrados, Bebidas vegetales (Bebida vegetal)
- **02.02 Café, té e infusiones:** Café (Café), Té e infusiones (Té), Cremoras y endulzantes para café
- **02.03 Bebidas alcohólicas:** Cerveza (Cerveza), Vinos (Vino), Licores (Ron), Mezcladores
- **02.04 Tabaco:** Cigarrillos

### 03 Cuidado personal y belleza

- **03.01 Cuidado del cabello:** Shampoo (Shampoo), Acondicionadores y tratamientos (Acondicionador), Cremas, geles y fijadores, Tintes (Tinte para cabello), Cepillos y accesorios para cabello
- **03.02 Higiene corporal:** Jabones y geles de baño (Jabón), Desodorantes (Desodorante), Cremas y lociones corporales, Protector solar, Fragancias, Accesorios de baño
- **03.03 Cuidado facial:** Limpieza y desmaquillantes (Agua micelar), Cremas y tratamientos faciales
- **03.04 Higiene bucal:** Pasta dental (Pasta dental), Cepillos dentales (Cepillo dental), Enjuague e hilo dental (Enjuague bucal)
- **03.05 Maquillaje y uñas:** Maquillaje, Uñas, Accesorios cosméticos
- **03.06 Afeitado y depilación:** Afeitado y depilación
- **03.07 Higiene femenina e íntima:** Toallas, protectores y tampones (Protector diario, Toalla femenina), Higiene íntima
- **03.08 Básicos de higiene:** Algodón, hisopos y pañuelos, Kits de viaje

### 04 Salud y farmacia

- **04.01 Medicamentos de venta libre:** Dolor y fiebre, Gripe, tos y alergias, Sistema digestivo, Dermatología, Cuidado de pies, Ojos y oídos, Antiparasitarios
- **04.02 Vitaminas y suplementos:** Vitaminas y minerales, Suplementos alimenticios, Nutrición deportiva, Control de diabetes
- **04.03 Primeros auxilios:** Primeros auxilios
- **04.04 Salud sexual e incontinencia:** Salud sexual, Incontinencia adulto
- **04.05 Óptica:** Lentes y cuidado visual

### 05 Bebé

- **05.01 Pañales y toallitas:** Pañales (Pañal), Toallitas húmedas (Toallita húmeda)
- **05.02 Alimentación del bebé:** Fórmulas y leches infantiles, Colados, cereales y galletas infantiles, Biberones y lactancia
- **05.03 Baño y cuidado del bebé:** Baño y cuidado del bebé
- **05.04 Accesorios, coches y cunas:** Accesorios, coches y cunas

### 06 Limpieza

- **06.01 Lavandería:** Detergentes (Detergente), Suavizantes (Suavizante), Jabón de lavandería, Quitamanchas, blanqueadores y colorantes
- **06.02 Limpieza del hogar:** Cloro y desinfectantes (Cloro, Desinfectante, Limpiador desinfectante), Limpiadores multiusos y de cocina (Limpiador), Lavaplatos (Lavaplatos), Insecticidas y control de plagas, Cuidado de pisos y calzado, Accesorios de limpieza
- **06.03 Papel y desechables:** Papel higiénico (Papel higiénico), Papel toalla y servilletas (Papel toalla, Servilleta), Bolsas para basura, Bolsas, aluminio y envolturas, Platos, vasos y cubiertos desechables
- **06.04 Aromatizantes:** Aromatizantes (Aromatizante)

### 07 Mascotas

- **07.01 Perros:** Alimento para perro (Alimento para perro), Snacks y premios para perro
- **07.02 Gatos:** Alimento para gato (Alimento para gato), Arena para gato
- **07.03 Otras mascotas:** Otras mascotas
- **07.04 Accesorios e higiene de mascotas:** Accesorios y juguetes para mascotas, Higiene y salud de mascotas

### 08 Hogar y cocina

- **08.01 Cocina:** Utensilios de cocina y repostería, Ollas y sartenes, Recipientes, termos y botellas
- **08.02 Mesa:** Vajillas, vasos y cubiertos
- **08.03 Blancos:** Sábanas, edredones y cobijas, Toallas, Almohadas y colchones
- **08.04 Decoración:** Decoración del hogar, Velas y aromaterapia, Decoración de temporada
- **08.05 Organización y lavandería:** Organización y almacenamiento
- **08.06 Muebles:** Muebles
- **08.07 Jardín y exteriores:** Jardinería, Muebles, parrillas y exteriores

### 09 Ferretería y autos

- **09.01 Ferretería:** Herramientas, Electricidad e iluminación, Pilas y baterías, Pintura, Plomería, pegamentos y selladores
- **09.02 Autos y motos:** Aceites y lubricantes, Llantas, Baterías para auto, Limpieza y accesorios para auto, Motos

### 10 Electrónica y electrodomésticos

- **10.01 Electrónica:** Televisores y accesorios, Audio, Celulares y accesorios, Computación y tablets, Videojuegos
- **10.02 Electrodomésticos:** Pequeños electrodomésticos, Línea blanca, Climatización

### 11 Ropa, calzado y accesorios

- **11.01 Ropa:** Hombre, Mujer, Niños, Niñas, Bebé
- **11.02 Calzado:** Calzado
- **11.03 Accesorios y equipaje:** Accesorios y equipaje

### 12 Juguetes

- **12.01 Juguetes:** Muñecas y peluches, Figuras de acción y coleccionables, Vehículos y control remoto, Juegos de mesa y rompecabezas, Bloques y construcción, Bebé y preescolar, Educativos, arte y manualidades, Juegos de exterior, Juegos de roles

### 13 Deportes y aire libre

- **13.01 Deportes:** Fitness y ejercicio, Artículos deportivos, Bicicletas, scooters y patines, Natación y piscinas
- **13.02 Aire libre:** Campismo

### 14 Papelería, oficina y fiestas

- **14.01 Papelería y oficina:** Útiles escolares y de oficina, Mochilas
- **14.02 Libros y entretenimiento:** Libros, música y películas
- **14.03 Fiestas y regalos:** Artículos para fiesta y envolturas

