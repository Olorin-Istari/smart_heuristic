# PoC — Busca Fragmentada (TREE) com Early-Stop (Rastrigin 1D)

Este repositório é um **espelho público (portfólio)** de uma Prova de Conceito (PoC) de **orquestração de busca paralela**:
- **TREE (fragmentação hierárquica)** do espaço de busca
- **Early-stop** (para todos assim que uma solução é encontrada)
- Métricas e gráficos reprodutíveis (**tempo**, **attempts**, **speedup**)

> Objetivo: demonstrar o método e a instrumentação.  
> Para aplicações reais (função objetivo do cliente), existe uma oferta de **PoC paga + relatório + integração**.

---

## O que tem aqui

### `prod/` (produção / estável)
Versão recomendada para rodar e reproduzir os resultados:
- `prod/src/rastrigin_poc.py` — roda FIND + BENCH + gera 3 gráficos
- `prod/results/example/` — exemplo de outputs (CSV/JSON/plots)

### `docs/`
Materiais de apresentação (PDF e figuras).

---

## Como rodar (Windows / VS Code)
1) Crie/ative um ambiente Python 3.11+ (recomendado)
2) Instale dependências:
   - `pip install matplotlib`
3) Abra e rode:
   - `prod/src/rastrigin_poc.py` (F5)

Os resultados serão salvos em:
- `prod/results/` (localmente)
- Os gráficos PNG serão gerados automaticamente.

---

## Como ler os gráficos

### 1) FIND — Attempts até achar (SEQ vs TREE, escala log)
Mostra quantas avaliações foram necessárias até atingir `f(x) < threshold`.
- A escala log evidencia **ordens de grandeza** entre SEQ e TREE.

### 2) FIND — Tempo até achar (SEQ vs TREE, escala log)
Mostra o tempo total até a primeira solução.
- O ganho vem do **método + early-stop**, não necessariamente de “mais ips por core”.

### 3) BENCH — Speedup efetivo (PAR/SEQ) vs Workers (60s)
Mede throughput sustentado em tempo fixo:
- evidencia saturação e overhead de runtime (especialmente em Python/Windows).

---

## Limitações (importante)
- Esta PoC foca em **orquestração e medição**, não em “resolver Rastrigin”.
- Em Python/Windows, processos/IPC adicionam overhead; em C++/CUDA o cenário muda.
- O comportamento do FIND pode variar conforme o agendamento e granularidade dos intervalos.

---

## Oferta (Consultoria / PoC paga)
Se você tem um processo com **busca grande** (calibração, tuning, simulação, testes, etc.) e quer reduzir custo/tempo:

**PoC de 10 dias úteis (paga):**
- adaptação do motor para a sua função objetivo
- medição antes/depois com gráficos e relatório curto
- recomendação de parâmetros (workers, granularidade, stop)

Para contato: (adicione seu e-mail/LinkedIn aqui)

---

## Licença
Veja o arquivo `LICENSE`.
