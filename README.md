# Pregoeiro

O pregão semanal de Lisboa — tudo o que abre, toca e acontece esta semana, por tema.
Um site estático que mostra os eventos da próxima semana na Grande Lisboa, com um motor
de recolha automática (domingo: eventos da semana; segunda: verificação de locais).

## Estrutura

```
docs/                 → o site (publicado pela Vercel; Root Directory = docs)
  index.html            página pública
  admin/                painel privado (/admin): login, recolha manual, alterações
  assets/               style.css · app.js
  data/                 semanas em JSON + alterações propostas
  taxonomy.json         15 temas · 49 categorias · bairros
sources/
  sources.json          lista-semente de ~1.150 fontes (563 com site recolhível)
  taxonomy.json          fonte de verdade dos temas/categorias/bairros
crawler/
  parse_directory.py    LISBON-EVENTS.md → sources.json
  make_sample_week.py   gera a semana de exemplo
config.yaml             âmbito, custo da IA, horários, larguras de recolha
LISBON-EVENTS.md        diretório-fonte (referência humana)
PUBLICAR.md             como publicar (Vercel + GitHub), passo a passo
```

## Estado

- ✅ Lista-semente, taxonomia, site público + admin (PT-PT), design e dados de exemplo.
- ⏳ Por construir: `crawler/crawl_events.py`, `crawler/check_sources.py` e os workflows do
  GitHub Actions (`crawl-events.yml`, `check-sources.yml`, `apply-changes.yml`).

## Publicar

Ver **[PUBLICAR.md](PUBLICAR.md)**. Resumo: código no GitHub → site na Vercel (Root Directory `docs`).

## Desenvolvimento local

```
py crawler/parse_directory.py     # reconstrói sources.json a partir do diretório
py crawler/make_sample_week.py    # regenera a semana de exemplo
py -m http.server 8770 --directory docs   # pré-visualizar em http://localhost:8770
```
