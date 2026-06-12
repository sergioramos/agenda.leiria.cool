// Credential check for /admin, executed in the Node.js runtime where
// environment variables are reliable (the edge-middleware runtime was not —
// see middleware.js). Returns only a status code; never echoes secrets.
//   204 = credentials match · 401 = wrong/missing · 503 = env vars not set
module.exports = (req, res) => {
  const user = process.env.ADMIN_USER || '';
  const pass = process.env.ADMIN_PASSWORD || '';
  res.setHeader('cache-control', 'no-store');

  if (!user || !pass) {
    res.statusCode = 503;
    res.end('env');
    return;
  }

  const header = req.headers.authorization || '';
  let ok = false;
  if (header.startsWith('Basic ')) {
    try {
      const text = Buffer.from(header.slice(6), 'base64').toString('utf8');
      const i = text.indexOf(':');
      ok = i > -1 && text.slice(0, i) === user && text.slice(i + 1) === pass;
    } catch { ok = false; }
  }
  res.statusCode = ok ? 204 : 401;
  res.end();
};
