# Publicar o Pregoeiro

Guia passo a passo, sem código. O site fica alojado na **Vercel**; o código e a recolha
semanal vivem no **GitHub**. Os dois são gratuitos.

> A pasta do site é `docs/`. Guarde esse pormenor — é preciso na Vercel (passo 2).

---

## 1 · Pôr o código no GitHub

1. Crie uma conta em **https://github.com** (se ainda não tiver).
2. Vá a **https://github.com/new** e crie um repositório:
   - **Repository name:** `pregoeiro`
   - **Public** (tem de ser público para a recolha semanal ser gratuita)
   - **NÃO** marque "Add a README" nem ".gitignore" (o projeto já os tem)
   - Clique **Create repository**.
3. Enviar esta pasta para esse repositório. Escolha **uma** das opções:

   **Opção A — GitHub Desktop (mais simples):**
   - Instale o GitHub Desktop: https://desktop.github.com
   - `File → Add local repository…` → escolha a pasta `lisbon-events`.
   - Clique **Publish repository** → nome `pregoeiro` → desmarque "Keep this code private" → **Publish**.

   **Opção B — linha de comandos** (cole, trocando `O-SEU-UTILIZADOR`):
   ```
   git remote add origin https://github.com/O-SEU-UTILIZADOR/pregoeiro.git
   git push -u origin main
   ```
   No primeiro `push` abre uma janela do navegador para iniciar sessão no GitHub. Autorize.

---

## 2 · Publicar o site na Vercel

1. Vá a **https://vercel.com** e clique **Sign Up** → **Continue with GitHub** (liga as contas).
2. **Add New… → Project**.
3. Na lista, escolha o repositório **pregoeiro** → **Import**.
4. Antes de publicar, abra **"Root Directory"** e selecione a pasta **`docs`**. *(Este é o passo que se costuma esquecer.)*
   - **Framework Preset:** Other
   - **Build Command:** deixar vazio
   - **Output Directory:** deixar vazio
5. Clique **Deploy**. Em ~1 minuto o site fica online em algo como **`pregoeiro.vercel.app`**.

> Sempre que a recolha de domingo enviar eventos novos para o GitHub, a Vercel volta a publicar sozinha.

### Domínio próprio (opcional)
Na Vercel: **Project → Settings → Domains → Add**, e siga as instruções para apontar o seu domínio.

---

## 3 · Proteger e ligar o painel de administração (`/admin`)

O `/admin` está protegido a sério, no servidor (Vercel Edge Middleware): sem as
credenciais certas, a página nem chega a carregar. Para definir as credenciais:

1. Na Vercel: projeto **pregoeiro** → **Settings → Environment Variables**.
2. Crie duas variáveis (ambiente **Production**):
   - **`ADMIN_USER`** — o utilizador que quer (ex.: `manuel`)
   - **`ADMIN_PASSWORD`** — uma palavra-passe forte (prefira letras/números; evite acentos)
3. **Redeploy** (obrigatório — as variáveis só entram em vigor num deploy novo):
   **Deployments → ⋯ no deployment mais recente → Redeploy**.
4. Abra `o-seu-site.vercel.app/admin` → o navegador pede utilizador e palavra-passe.
5. Já dentro do painel, em **Definições**, preencha:
   - **Repositório:** `O-SEU-UTILIZADOR/pregoeiro`
   - **Token de acesso:** um *fine-grained token* do GitHub (passo 4).

---

## 4 · Token do GitHub (dá poder aos botões do painel)

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. **Repository access:** Only select repositories → `pregoeiro`.
3. **Permissions → Repository permissions:**
   - **Actions:** Read and write (botões «correr agora»)
   - **Contents:** Read and write (aplicar alterações e guardar limites de custo)
   - **Secrets:** Read and write (guardar a chave DeepSeek a partir do painel)
4. **Generate token**, copie-o e cole-o **só** no painel `/admin` (passo 1 · GitHub). Fica apenas no seu navegador.

---

## 5 · Chave da IA (para a recolha de eventos)

A recolha usa a DeepSeek. A forma mais fácil é **pelo painel** `/admin` →
secção «Chave DeepSeek»: cole a chave e clique **Guardar no GitHub** (é cifrada
no navegador e guardada como segredo do repositório). No mesmo painel pode ver
o saldo, o gasto do mês e ajustar os limites de custo.

Alternativa manual: GitHub → repositório `pregoeiro` → **Settings → Secrets and
variables → Actions → New repository secret** → Name `DEEPSEEK_API_KEY`.

> Lembrete único do GitHub: em **Settings → Actions → General → Workflow
> permissions**, escolha **Read and write permissions** (deixa a recolha
> publicar os eventos no repositório).
