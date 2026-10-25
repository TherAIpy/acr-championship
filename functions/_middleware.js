export async function onRequest(context) {
  const url = new URL(context.request.url);

  // Se la richiesta è per una pagina "pulita" (es. /standings),
  // prova a servire il file .html corrispondente internamente.
  if (!url.pathname.includes('.') && url.pathname !== '/') {
    const htmlPath = url.pathname + '.html';
    const htmlUrl = new URL(htmlPath, url.origin);

    // Chiediamo a Cloudflare di recuperare il file .html
    const assetResponse = await context.env.ASSETS.fetch(
      new Request(htmlUrl.toString(), context.request)
    );

    // Se il file .html esiste, restituiamo il suo contenuto con status 200.
    // Il browser non vede mai il redirect 308 e l'URL rimane /standings.
    if (assetResponse.status === 200) {
      return new Response(assetResponse.body, {
        status: 200,
        headers: assetResponse.headers,
      });
    }
  }

  // Per tutto il resto (inclusi i file .css, .js, immagini, ecc.)
  // lasciamo che Cloudflare gestisca normalmente la richiesta.
  return context.next();
}