// Vercel Edge Middleware — server-side protection for /admin.
// Credentials come from the project's Environment Variables (ADMIN_USER /
// ADMIN_PASSWORD), set only in the Vercel dashboard — never in this repo.
// Fail-closed: while the variables are missing, /admin answers 503.

export const config = { matcher: ['/admin', '/admin/:path*'] };

function decodeBasic(header) {
  if (!header || !header.startsWith('Basic ')) return null;
  try {
    const bytes = Uint8Array.from(atob(header.slice(6)), (c) => c.charCodeAt(0));
    const text = new TextDecoder().decode(bytes); // UTF-8-safe
    const i = text.indexOf(':');
    return i < 0 ? null : [text.slice(0, i), text.slice(i + 1)];
  } catch {
    return null;
  }
}

export default function middleware(request) {
  const user = process.env.ADMIN_USER;
  const pass = process.env.ADMIN_PASSWORD;

  if (!user || !pass) {
    return new Response(
      'Área de administração bloqueada: defina ADMIN_USER e ADMIN_PASSWORD em ' +
      'Settings → Environment Variables do projeto na Vercel e faça redeploy.',
      { status: 503, headers: { 'content-type': 'text/plain; charset=utf-8' } },
    );
  }

  const creds = decodeBasic(request.headers.get('authorization'));
  if (creds && creds[0] === user && creds[1] === pass) {
    return; // authenticated — continue to the static page
  }

  return new Response('Autenticação necessária.', {
    status: 401,
    headers: {
      'www-authenticate': 'Basic realm="Pregoeiro Admin", charset="UTF-8"',
      'content-type': 'text/plain; charset=utf-8',
    },
  });
}
