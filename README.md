# BGH Smart Control - Home Assistant Integration

Integración personalizada para controlar aires acondicionados BGH Smart vía UDP (protocolo local) para Home Assistant.

## Características

✅ Control completo del aire acondicionado:
- Encendido/Apagado
- Modos: Frío, Calor, Ventilación, Dry, Auto
- Velocidad del ventilador: Baja, Media, Alta
- Seteo de la temperatura objetivo
- Lectura de temperatura ambiente
- Lectura de temperatura objetivo (setpoint)

✅ Comunicación local vía UDP (no requiere cloud)
✅ Actualización automática cada 10 segundos
✅ Configuración vía UI (no requiere editar YAML)
✅ Soporte para múltiples equipos

## Requisitos

- Home Assistant 2023.1 o superior
- Aire acondicionado BGH Smart con control IP/WiFi
- IP fija configurada en tu router para cada equipo

## Instalación

### Opción 1: HACS (Recomendado)

1. Abre HACS en Home Assistant
2. Ve a "Integraciones"
3. Haz clic en los tres puntos (⋮) arriba a la derecha
4. Selecciona "Repositorios personalizados"
5. Agrega esta URL: `https://github.com/firtman/bgh_smart`
6. Categoría: `Integration`
7. Busca "BGH Smart Control" y descárgala
8. Reinicia Home Assistant

### Opción 2: Manual

1. Copia la carpeta `bgh_smart` a `config/custom_components/`
2. La estructura debe quedar así:
   ```
   config/
   └── custom_components/
       └── bgh_smart/
           ├── __init__.py
           ├── manifest.json
           ├── climate.py
           ├── config_flow.py
           ├── const.py
           ├── bgh_client.py
           ├── coordinator.py
           ├── strings.json
           └── translations/
               ├── en.json
               └── es.json
   ```
3. Reinicia Home Assistant

## Configuración

### Paso 1: Configurar IPs Fijas

**Importante:** Antes de configurar, asigna IPs fijas a tus aires en tu router DHCP.

Ejemplo:
- Living: `192.168.2.169`
- Dormitorio 1: `192.168.2.170`
- Dormitorio 2: `192.168.2.171`

### Paso 2: Agregar la Integración

1. Ve a **Configuración** → **Dispositivos y servicios**
2. Haz clic en **+ AGREGAR INTEGRACIÓN**
3. Busca "BGH Smart Control"
4. Completa el formulario:
   - **Nombre**: Nombre descriptivo (ej: "AAC Living")
   - **IP**: Dirección IP del equipo (ej: 192.168.2.169)
5. Haz clic en **ENVIAR**

Repite para cada aire acondicionado.

### Paso 3: ¡Listo!

Tus aires aparecerán como entidades `climate` en Home Assistant.

## Uso

### En la UI de Home Assistant

Los aires aparecen en el panel de control como cualquier termostato:

- **Encender/Apagar**: Botón de power
- **Modo**: Selecciona entre Frío, Calor, Ventilación, Dry, Auto
- **Ventilador**: Baja, Media, Alta
- **Temperatura actual**: Muestra la temperatura ambiente
- **Setpoint**: Muestra la temperatura configurada en el equipo y permite cambiarla

### En Automatizaciones

```yaml
# Ejemplo: Encender aire en modo frío a las 18:00
automation:
  - alias: "Encender AAC Living"
    trigger:
      - platform: time
        at: "18:00:00"
    action:
      - service: climate.set_hvac_mode
        target:
          entity_id: climate.aac_living
        data:
          hvac_mode: cool
      - service: climate.set_fan_mode
        target:
          entity_id: climate.aac_living
        data:
          fan_mode: medium
```

```yaml
# Ejemplo: Apagar cuando no hay nadie en casa
automation:
  - alias: "Apagar AAC al salir"
    trigger:
      - platform: state
        entity_id: binary_sensor.anyone_home
        to: 'off'
        for:
          minutes: 5
    action:
      - service: climate.turn_off
        target:
          entity_id: climate.aac_living
```

### En Scripts

```yaml
script:
  verano_noche:
    alias: "Modo verano - Noche"
    sequence:
      - service: climate.set_hvac_mode
        target:
          entity_id:
            - climate.aac_living
            - climate.aac_dormitorio_1
        data:
          hvac_mode: cool
      - service: climate.set_fan_mode
        target:
          entity_id:
            - climate.aac_living
            - climate.aac_dormitorio_1
        data:
          fan_mode: low
```

## Servicios Disponibles

### climate.set_hvac_mode
Cambia el modo de operación.

**Modos disponibles:**
- `off`: Apagado
- `cool`: Frío
- `heat`: Calor
- `dry`: Deshumidificación
- `fan_only`: Solo ventilación
- `auto`: Automático

### climate.set_fan_mode
Cambia la velocidad del ventilador.

**Velocidades:**
- `low`: Baja
- `medium`: Media
- `high`: Alta

### climate.turn_on / climate.turn_off
Enciende o apaga el equipo.

## Troubleshooting

### El equipo no se conecta

1. Verifica que la IP sea correcta
2. Asegúrate de que el aire esté encendido
3. Verifica que Home Assistant y el aire estén en la misma red
4. Prueba hacer ping a la IP del aire desde Home Assistant:
   ```bash
   ping 192.168.2.169
   ```

### Los comandos no funcionan

1. Verifica los logs de Home Assistant:
   - **Configuración** → **Sistema** → **Logs**
   - Busca errores relacionados con `bgh_smart`
2. Verifica que no haya firewall bloqueando el puerto UDP 20910/20911

### El estado no se actualiza

1. La integración consulta el estado cada 10 segundos
2. Si usas el control remoto físico, el cambio se reflejará en la próxima actualización
3. Puedes forzar una actualización recargando la integración

## Limitaciones Conocidas

- **Set temperature no implementado**: El protocolo BGH no parece soportar cambio de temperatura vía UDP. El setpoint se lee pero no se puede modificar desde HA.
- **Sin modo swing**: No está implementado en el protocolo UDP básico.
- **Polling**: La integración consulta el estado cada 10 segundos (no es push).

## Protocolo UDP

El aire BGH Smart funciona con un protocolo **UDP broadcast**:

**Envío de comandos:**
- Puerto destino: 20910
- Dirección: IP del aire (ej: 192.168.5.88)

**Recepción de estado:**
- Puerto de escucha: 20911
- El aire envía **broadcasts** a 255.255.255.255:20911
- Cada vez que cambia el estado, el aire publica automáticamente
- **No es request/response**, es **publish/subscribe**

**Comando de control:**
```
00000000000000accf23aa3190f60001610402000080
Byte 17: Modo (0=off, 1=cool, 2=heat, 3=dry, 4=fan, 254=auto)
Byte 18: Velocidad (1=low, 2=medium, 3=high)
```

**Comando de status request (opcional):**
```
00000000000000accf23aa3190590001e4
```
Esto hace que el aire envíe un broadcast con su estado actual.

**Formato de broadcast (29 bytes):**
```
Desde: 192.168.5.88:20910
Hacia: 255.255.255.255:20911

Byte 18: Modo actual
Byte 19: Velocidad actual
Bytes 21-22: Temperatura ambiente (little-endian, /100)
Bytes 23-24: Setpoint (little-endian, /100)
```

## Créditos

Basado en ingeniería inversa del protocolo BGH Smart Control UDP luego que BGH decida dejar de dar servicio desde 2026 por la desaparición de la empresa proveedora del servicio Solidmation (cerró en 2018).

## Licencia

MIT License

## Contribuciones

Issues y Pull Requests son bienvenidos!

---

¿Problemas? Abre un issue en GitHub.
