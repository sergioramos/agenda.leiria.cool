// Vercel Edge Middleware — server-side protection for /admin.
//
// The middleware itself does NOT read the credentials: on this static
// project the edge runtime returned empty env vars no matter how they were
// accessed (verified with deployed probes). Instead it delegates the check
// to /api/auth — a Node serverless function, where process.env is reliable.
// Credentials live only in the Vercel env vars (ADMIN_USER/ADMIN_PASSWORD).
// Fail-closed: missing vars → 503; wrong/missing credentials → 401.

export const config = { matcher: ['/admin', '/admin/:path*'] };

export default async function middleware(request) {
  let verdict = 0;
  try {
    const res = await fetch(new URL('/api/auth', request.url), {
      headers: { authorization: request.headers.get('authorization') || '' },
      cache: 'no-store',
    });
    verdict = res.status;
  } catch {
    verdict = 0; // network failure → treat as locked
  }

  if (verdict === 204) {
    return; // authenticated — continue to the static page
  }

  if (verdict === 503) {
    return new Response(
      'Área de administração bloqueada: defina ADMIN_USER e ADMIN_PASSWORD em ' +
      'Settings → Environment Variables do projeto na Vercel e faça redeploy.',
      { status: 503, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' } },
    );
  }

  return new Response('Autenticação necessária.', {
    status: 401,
    headers: {
      'www-authenticate': 'Basic realm="Pregoeiro Admin", charset="UTF-8"',
      'content-type': 'text/plain; charset=utf-8',
      'cache-control': 'no-store',
    },
  });
}
