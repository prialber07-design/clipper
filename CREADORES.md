# Qué clipea la comunidad de cada creador

Datos reales de los clips más vistos de cada canal (Twitch y API de Kick,
agosto 2026). Sirve para escribir ganchos que encajen con lo que su gente
comparte, en vez de con lo que a mí me parezca interesante.

## El patrón que se repite en los diez

**La duración.** Los clips que de verdad circulan duran **4–30 segundos**. El
más visto de Lopezfnx dura 9 s; el segundo, 4 s. El de RdJavi con 383.865
visitas dura 15 s. Sesenta segundos es la excepción, no la norma.

**El tema.** Casi todo es social: ligoteo, tensión sexual no resuelta, vergüenza
ajena, sustos, caídas y peleas. Nada de gameplay ni de logística.

**Los títulos.** Su comunidad no titula con gancho de marketing: usa la reacción
en crudo (`EEEE`, `QUE SUSTO JAJAJAJJA`, `ehhh?`) o describe el hecho seco
(`DAVO ROBANDO COMO SIEMPRE`). El clickbait elaborado no es su idioma.

**Comparten universo.** El Calvo, Franbeuve y Lopezfnx salen en los clips unos
de otros; Davoo y La Cobra están siempre juntos. Un clip que junte a dos vale
por dos audiencias.

---

## Antes del primer clip de un canal: mirar cómo emite

El layout se asigna a ojo al añadir el canal y **casi siempre está mal**. Ha
mordido dos veces el mismo día: Elxokas estaba en `reaccion` y emite League of
Legends a pantalla completa; Peereira igual, y emite a cámara completa. Los dos
habrían salido con una franja vacía haciendo de webcam.

Cuesta un minuto comprobarlo, sacando un fotograma del buffer:

```
python clipper.py mosaico <slug>
```

| Canal | Layout | ¿Comprobado? |
|---|---|---|
| elxokas | `completo` | sí · LoL a pantalla completa |
| peereira7 | `completo` | sí · Just Chatting, llena el cuadro |
| zonagemelosyt | `completo` | sí · plano fijo de la casa |
| lopezfnx | `reaccion` | sí · IRL con el móvil en horizontal |
| franbeuve | `reaccion` | sí · IRL de sobremesa |
| elcalvolol · reydelacity | `irl` | sí · IRL de calle |
| **davooxeneize** | `reaccion` | **NO** |
| **lacobraaa** | `reaccion` | **NO** |
| **rdjavi** | `reaccion` | **NO** |
| mariotaxi · viviendoenlacalle | `irl` | NO |
| josenogales1 · agustin51 | `reaccion` | NO |
| zonagemelosoficial | `completo` | NO |

Los tres primeros en negrita son **los de más audiencia de toda la lista** (2,03 M,
1,83 M y 969 k). Cuando alguno entre en directo, lo primero es mirar el
fotograma; lo segundo, escribirle el gancho.

## Peereira7 · el caso que hay que copiar

Es el creador del clip que mejor ha rendido de todos los analizados: **215.600
visitas desde una cuenta de 1.742 seguidores**, con un 9,3 % de compartidos.
Ver la sección de estilos en `ESTILO.md`.

**Su comunidad se clipea a sí misma en clave de broma.** Sus clips más vistos
del último mes, con los títulos que le pone su propia gente:

| Clip | Visitas | Duración |
|---|---|---|
| GOL ANULADO DE ESPAÑA | 24.536 | **0:10** |
| GOL DE ESPAÑA | 10.480 | 0:29 |
| peillo | 4.225 | 0:30 |
| bailecito | 4.033 | 0:26 |
| pillan a pepe fumando | 3.125 | 0:29 |
| Urko se comió que? | 2.967 | 0:21 |

**Dos filones:** el fútbol de la selección (los dos clips más vistos, con
diferencia) y **el caos del grupo de amigos** — Urko, Pepe, Kata, Willy. Los
nombres propios del grupo valen más que cualquier tema.

**Su jerga:** `vqmoooooooss`, `MY BAD 🙏`, `peillo`, y el apodo que le puso la
comunidad, **castor** (`#pereiracastor🦫`).

Duración: casi todo entre **10 y 30 segundos**. El más visto dura diez.

## A quién estamos clipeando de verdad (2 de agosto de 2026)

Repasando los 32 clips publicados, el reparto no se parece en nada al tamaño de
las audiencias:

| Canal | Seguidores | Clips publicados |
|---|---|---|
| **Davoo Xeneize** | **2.031.801** | **0** |
| **La Cobra** | **1.832.913** | **0** |
| **RDJavi** | **969.536** | **0** |
| Zona Gemelos (Kick) | 192.370 | 0 |
| Rey de la City | 159.477 | **14** |
| El Calvo | 66.075 | **10** |

**24 de 32 clips (75 %) son de los dos creadores más pequeños**, y los tres
millonarios no tienen ni uno. El canal de clips que estudiamos hace lo
contrario: 13 de sus 18 publicaciones son de Xokas.

No es un fallo del sistema: se comprobó canal por canal y los grandes estaban
realmente offline. El motivo es el **horario**. Davoo, La Cobra y RDJavi son
argentinos y emiten de madrugada hora española, justo cuando no hay nadie
revisando ganchos. Rey de la City y El Calvo emiten a todas horas, así que
copan la cola.

**Qué hacer con esto:** cuando uno de los grandes esté en directo, sus clips van
primero aunque haya cola de otros. Un clip de Davoo tiene trece veces más
audiencia potencial que uno de Rey de la City, y el esfuerzo de escribir el
gancho es el mismo.

---

## Twitch

### El Calvo (`elcalvolol`) · 66.075 seguidores
Vergüenza ajena y ligoteo. Momentos físicos y sociales, nunca de juego.

| Clip | Visitas | Duración |
|---|---|---|
| Beso del calvo | 7.310 | 0:29 |
| cobrilla de Calvo a Iratxe | 7.168 | 0:20 |
| que miras calvo | 5.862 | **0:07** |
| caída x2 | 5.532 | 1:00 |
| MOMENTO INCÓMODO | 5.200 | 0:23 |

**Buscar:** que ligue y le salga mal, tropiezos, silencios incómodos, que le
pillen mirando.

### Lopezfnx · IRL, ~610 espectadores de media
**Iratxe es el motor.** Sus diez clips más vistos son todos tensión romántica
con ella, y todos de los últimos once días.

| Clip | Visitas | Duración |
|---|---|---|
| SEMICUCHARITA DE IRATXE | 32.579 | **0:09** |
| Iratxe enamorada? | 15.238 | **0:04** |
| beso durmiendo? y epa esa mano | 7.721 | 0:23 |
| tocate el pelo si hay tensión secsual no resuelta | 5.502 | 0:08 |

**Buscar:** cualquier roce, mirada o gesto entre ellos. Aquí un clip de cuatro
segundos vale más que uno de sesenta.

### Franbeuve
Igual que El Calvo pero más bruto. Cruza mucho con él.

`TENIA UNAS TETAS` (7.039) · `y a seguir` (6.354, **0:04**) ·
`BESO DE CALVO (HISTORICO)` (4.420) · `A DONDE MIRAS FRAN` (2.479)

### José Nogales (`josenogales1`)
Marca propia: **looksmaxxing**. Escala PSL, control del cortisol, proyección
mandibular. Lo dice todo con seriedad absoluta, y ahí está el filón.

`liada historica` (14.282, **0:09**) · `pim pam` (6.974) ·
`uf casi se lia` (6.487) · `oso pone las cosas claras`

**Buscar:** que analice el físico de alguien famoso, afirmaciones tajantes sobre
cortisol o testosterona, confesiones sin filtro.

---

## Kick

### Davoo Xeneize · 2,03M seguidores
Fútbol argentino, Boca, y su dinámica con La Cobra.

| Clip | Visitas | Duración |
|---|---|---|
| EEEE | 238.501 | **0:19** |
| DAVO ROBANDO COMO SIEMPRE | 127.892 | **0:16** |
| Los Chistes de Davo | 74.178 | 0:50 |
| ROBO ROBO, UN MALDITO ROBO | 58.314 | 0:32 |
| se asoma un raton camara vidal | 26.781 | 0:15 |

**Buscar:** indignación arbitral, chistes, sustos. Sus dos mejores duran 19 y
16 segundos.

### La Cobra (`lacobraaa`) · 1,83M
Reacciones físicas y el dúo con Davoo.

`Baile Cobra + Davo` (211.579) · `y esa mano cobra` (122.673, 0:20) ·
`La raja del gordo` (36.727) · `Casi se infarta lo cobra`

### RdJavi · 967k
**El de mayor viralidad por clip de los diez.** Humor de reacción y sustos.

| Clip | Visitas | Duración |
|---|---|---|
| eran pocos en el carro JAJAJJAAJA | **383.865** | **0:15** |
| un banco pa rd javi | 177.152 | 0:30 |
| CLIP DE LA PALABRA CLAVE | 56.621 | 0:30 |
| QUE SUSTO JAJAJAJJA | 47.763 | 0:30 |

**Buscar:** sustos, risas descontroladas, momentos absurdos. Ninguno de sus
grandes pasa de 30 segundos.

### Rey de la city · 158k
Volumen bajo (19k el mejor) y títulos sin información (`gg`, `ssss`, `U`).
Su comunidad clipea sin criterio, así que **hay poca señal que aprovechar**.

### Mario Taxi · 6k
Contenido de VTC y tráfico. Su mejor clip tiene **594 visitas**: el techo es muy
bajo. `VTC le pega a bici` · `Semaforo` · `Timing perfecto`

### Viviendo en la calle · 5,2k
Igual: máximo 1.580 visitas. `ah!` · `yo flipo tio` · `Se cae yony pobrecito`

---

## Qué están haciendo ahora (revisado 2026-08-01)

Esto caduca rápido: revisar cada pocas semanas.

**Los cuatro de Twitch están en el mismo circuito.** Tour IRL por España, La
Velada del Año en Sevilla (ya pasada, "post Velada") y festivales de verano
(Arenal Sound, Share Festival). Se cruzan constantemente: El Calvo y Lopezfnx
coinciden en el Share Festival de Barcelona, Franbeuve y El Calvo van juntos
a la Velada, y Lopezfnx+Iratxe se cruzan también con El Calvo ("Iratxe y
López", 2.121 vistas en el canal de El Calvo).

- **El Calvo** — Tour IRL España (Andalucía, Sevilla, Valencia, Cataluña) y
  ahora post-Velada en Arenal Sound con artistas (Juseph, Omar Montes,
  Danirep) y Paula Monnet. **Nuevo running gag: Toni.** Se le cae el
  invisalign, le tiran un huevo, "Doble Huevazo a Toni" — tres clips
  distintos en el último mes. También un meme suelto: clips titulados
  simplemente *"67"* (dos veces). Directos de 4–6 h.
- **Lopezfnx** — Sigue siendo todo con **Iratxe**: IRL Barcelona, anuncio de
  una serie juntos. Top clips del mes: *"SEMICUCHARITA DE IRATXE"* (32.610),
  *"Iratxe enamorada?"* (15.267). Sin cambios de fondo.
- **Franbeuve** — Vuelta tras la Velada, día 2–3 en su pueblo (moto
  eléctrica, FIFA, OmeTV), luego "short stream" de vuelta. Clips nuevos con
  algo de tracción: *"cama"* (573), *"como se cae"* (397). Volumen bajo en
  general (mejor clip del mes: 720 vistas).
- **José Nogales** — Sigue en el reality (*La casa del gemelo*), directos
  cortos e irregulares de madrugada, más un DJ set en Cádiz/Magaluf. **Nuevo
  nombre recurrente: Aitana** — *"aitana"* (746) y *"aitana y jose"* (420),
  ambos hace 21 días. Confirma lo ya sabido: el looksmaxxing es su discurso,
  pero su comunidad sigue clipeando lo social (*"liada historica"*, *"el
  trio"*, *"cadera de muñeka"*).

**Kick, último mes:**

- **La Cobra** — Sigue viviendo del **fútbol** (Mundial 2026 llegando a su
  fin): *"tercer gol"*, *"GOLAZO CABO VERDE"*, *"GOL DE ENZO FERNANDEZ
  ARGENTINA VS INGLATERRA"*. Sin cambios.
- **Davoo** — Cerró su etapa en España (*"ULTIMO STREAM EN SEVILLA"*) y
  volvió a Just Chatting habitual. Sin temas nuevos con tracción suficiente
  para añadir (vistas del último mes muy bajas, 200–420).
- **RdJavi** — Sigue en **GTA V**, pero el volumen de clips del último mes es
  bajo (máx. 580 vistas) y sin títulos con señal clara. Sin cambios.
- **Rey de la city** — Sin señal aprovechable, como siempre (*"gg"*, *"ssss"*,
  *"TYTY"*).
- **Mario Taxi** — **No está dormido**: varios directos largos recientes
  (hasta 11,9 h) con VTC de noche y, en varias sesiones, "noche de Visage"
  (juego de terror) tras acabar las carreras. Formato mixto taxi+terror
  nocturno; se añade `visage` a temas. Sus clips de Twitch no se han podido
  recoger todavía (los pinta JS y quedó fuera del muestreo de esta semana).
- **Viviendo en la calle** — Confirmado sin clips en el último mes. Sigue
  dormido.

---

## Qué hacer con esto

**Prioridad por rendimiento esperado:** RdJavi y Davoo (viralidad por clip muy
alta) → Lopezfnx (motor claro con Iratxe) → El Calvo, Franbeuve, José Nogales,
La Cobra → Rey de la city → Mario Taxi y Viviendo en la calle.

**Los diez canales se mantienen** (decidido el 2026-08-01). Mario Taxi y
Viviendo en la calle tienen techos bajos —594 y 1.580 visitas— pero son canales
pequeños en crecimiento y vigilarlos no cuesta nada mientras estén offline la
mayor parte del día. No volver a proponer quitarlos.

**Las tres franjas de duración se mantienen** tal cual: corto 10–20 s, medio
26–34 s, largo 64–95 s, con el patrón cíclico. No bajar el corto a 5–10 s.
La prioridad es tener siempre material que monetice en TikTok, aunque los
clips de 4–9 s sean los que más circulan en estas comunidades.
