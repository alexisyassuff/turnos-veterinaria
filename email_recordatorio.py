"""Plantilla HTML del mail de recordatorio de turnos - v3.

Separado de tasks.py a proposito: el diseño del mail (colores, textos,
markup) no tiene nada que ver con la logica de Celery/SMTP, y mezclarlos
en el mismo archivo haria mas dificil tocar uno sin pisar el otro.
`construir_html` solo arma y devuelve el string HTML; quien lo llama
(tasks.py) decide como enviarlo.

El layout usa tablas anidadas con estilos inline (en vez de flexbox/grid
o un <style> en el head) porque es la forma que mejor soportan los
clientes de mail (Gmail, Outlook, etc.), que ignoran o recortan CSS
moderno.
"""


def construir_html(id_turno, vet, dueno, mascota, fecha, hora):
    return f"""\
<!DOCTYPE html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Recordatorio de turno</title>
  </head>
  <body style="margin:0; padding:0; background-color:#f2ede4; font-family: Verdana, Geneva, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f2ede4; padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px; background-color:#ffffff; border-radius:16px; overflow:hidden; box-shadow:0 4px 14px rgba(0,0,0,0.08);">

            <!-- Encabezado -->
            <tr>
              <td style="background:#2c7a6f; background-image:linear-gradient(135deg, #3fa796 0%, #2c7a6f 100%); padding:28px 24px; text-align:center;">
                <div style="font-size:34px; line-height:1;">&#128062;</div>
                <div style="color:#ffffff; font-size:22px; font-weight:bold; margin-top:8px; font-family: Verdana, Geneva, sans-serif;">
                  &iexcl;Ten&eacute;s un turno pr&oacute;ximo!
                </div>
              </td>
            </tr>

            <!-- Saludo -->
            <tr>
              <td style="padding:28px 28px 8px 28px;">
                <p style="margin:0; font-size:16px; color:#2d2d2d;">
                  Hola <strong>{dueno}</strong> &#128075;
                </p>
                <p style="margin:12px 0 0 0; font-size:15px; color:#555555; line-height:1.5;">
                  Te escribimos para recordarte que <strong>{mascota}</strong> tiene un turno agendado. &iexcl;Te esperamos!
                </p>
              </td>
            </tr>

            <!-- Tarjeta de detalles -->
            <tr>
              <td style="padding:20px 28px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f6faf9; border:1px solid #d9ece8; border-radius:12px;">
                  <tr>
                    <td style="padding:18px 20px;">
                      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px; color:#2d2d2d;">
                        <tr>
                          <td style="padding:6px 0; color:#2c7a6f;">&#128273; &nbsp;Turno</td>
                          <td style="padding:6px 0; text-align:right; font-weight:bold;">#{id_turno}</td>
                        </tr>
                        <tr>
                          <td style="padding:6px 0; color:#2c7a6f;">&#128054; &nbsp;Mascota</td>
                          <td style="padding:6px 0; text-align:right; font-weight:bold;">{mascota}</td>
                        </tr>
                        <tr>
                          <td style="padding:6px 0; color:#2c7a6f;">&#129658; &nbsp;Veterinario/a</td>
                          <td style="padding:6px 0; text-align:right; font-weight:bold;">{vet}</td>
                        </tr>
                        <tr>
                          <td style="padding:6px 0; color:#2c7a6f;">&#128197; &nbsp;Fecha</td>
                          <td style="padding:6px 0; text-align:right; font-weight:bold;">{fecha}</td>
                        </tr>
                        <tr>
                          <td style="padding:6px 0; color:#2c7a6f;">&#128336; &nbsp;Hora</td>
                          <td style="padding:6px 0; text-align:right; font-weight:bold;">{hora}</td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <!-- Aviso -->
            <tr>
              <td style="padding:4px 28px 20px 28px;">
                <p style="margin:0; font-size:14px; color:#555555; line-height:1.5;">
                  Escribinos para <strong>confirmar tu asistencia</strong> o <strong>cancelar el turno</strong> y liberar el lugar para otra familia peluda. &iexcl;Muchas gracias! &#128062;
                </p>
              </td>
            </tr>

            <!-- Boton de WhatsApp -->
            <tr>
              <td style="padding:0 28px 28px 28px; text-align:center;">
                <a href="https://wa.me/542612568523" style="display:inline-block; background-color:#25D366; color:#ffffff; text-decoration:none; font-weight:bold; font-size:14px; padding:12px 26px; border-radius:30px; font-family: Verdana, Geneva, sans-serif;">
                  &#128241; Escribinos por WhatsApp
                </a>
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td style="background-color:#f6faf9; padding:16px 28px; text-align:center; border-top:1px solid #e5e5e5;">
                <p style="margin:0; font-size:12px; color:#999999;">
                  Este es un recordatorio autom&aacute;tico. Si ya confirmaste o cancelaste este turno, pod&eacute;s ignorar este mensaje.
                </p>
              </td>
            </tr>

          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
