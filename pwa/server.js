/**
 * Serveur HTTPS personnalise pour le dev Next.js.
 *
 * Necessaire parce que Safari iOS bloque l'API Geolocation sur les pages
 * HTTP non-localhost (contexte non securise). En servant le PWA en HTTPS
 * (certificat auto-signe), Safari accepte la geolocation une fois
 * l'avertissement de certificat accepte.
 *
 * Les rewrites /api/* -> backend JARVIS sont geres par next.config.js
 * (proxy server-side, donc le backend peut rester en HTTP).
 */

const { createServer } = require('https');
const { parse } = require('url');
const next = require('next');
const fs = require('fs');
const path = require('path');

const dev = process.env.NODE_ENV !== 'production';
const hostname = '0.0.0.0';
const port = parseInt(process.env.PORT || '3000', 10);

const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();

const certDir = path.join(__dirname, 'certificates');
const httpsOptions = {
  key: fs.readFileSync(path.join(certDir, 'localhost-key.pem')),
  cert: fs.readFileSync(path.join(certDir, 'localhost.pem')),
};

app.prepare().then(() => {
  createServer(httpsOptions, (req, res) => {
    const parsedUrl = parse(req.url, true);
    handle(req, res, parsedUrl);
  }).listen(port, hostname, (err) => {
    if (err) throw err;
    console.log(`> Ready on https://localhost:${port}`);
    console.log(`> Network:  https://${hostname}:${port}`);
  });
});
