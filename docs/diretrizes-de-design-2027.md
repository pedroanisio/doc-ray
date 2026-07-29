---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
---

# Diretrizes de Design 2027

> Companion artifact: [print PDF](diretrizes-de-design-2027.pdf). The Markdown
> source and released PDF are bound by the checked
> [SHA-256 manifest](diretrizes-de-design-2027.pdf.sha256).

### Princípios, padrões e sistemas para produtos digitais

**Edição em Português (Brasil)**

---

*Um manual de referência para times de produto, design e engenharia que
projetam interfaces digitais. Reúne princípios de percepção humana, linguagem
visual, padrões de interface, sistemas de design, comunicação de dados,
acessibilidade e ética — organizados para consulta rápida e decisão prática.*

---

## Sumário

**Prefácio** — Por que diretrizes, e por que agora
**Como usar este livro**

**Parte I — Fundamentos**
1. O que é bom design
2. Como as pessoas percebem e decidem
3. Affordances, sinais e feedback

**Parte II — Linguagem visual**
4. Tipografia
5. Cor
6. Grid, layout e composição
7. Hierarquia visual e espaço

**Parte III — Padrões de interface**
8. Componentes e padrões de UI
9. Formulários
10. Navegação e arquitetura da informação

**Parte IV — Sistemas de design**
11. O que é um design system
12. Tokens, componentes e documentação
13. DesignOps e governança

**Parte V — Dados e comunicação visual**
14. Visualização de dados e dashboards
15. Infográficos e narrativa visual

**Parte VI — Qualidade, ética e processo**
16. Acessibilidade
17. Design persuasivo e ético
18. Pesquisa, testes e melhoria contínua

**Apêndices**
A. Checklists de bolso
B. Glossário
C. Bibliografia

---

# Prefácio — Por que diretrizes, e por que agora

Todo produto digital é uma sequência de decisões. Onde colocar o botão, que
palavra usar no rótulo, quanto espaço deixar respirar, qual cor carrega
significado e qual apenas decora. Isoladamente, cada decisão parece pequena.
Somadas, elas determinam se uma pessoa consegue fazer o que veio fazer — ou
desiste no meio do caminho.

Diretrizes existem para que essas decisões não sejam tomadas do zero a cada
tela. Elas transformam o que designers experientes sabem por intuição em
critérios explícitos, compartilháveis e discutíveis. Uma boa diretriz não
engessa: ela remove a discussão sobre o trivial para liberar energia para o que
é realmente difícil e específico do seu produto.

Este livro parte de uma premissa simples: **design é uma disciplina, não um
gosto.** Muito do que faz uma interface funcionar decorre de como a percepção
humana opera — leis descritas há décadas na psicologia experimental e na teoria
da informação, e desde então confirmadas em milhares de produtos. Sobre essa
base estável, acrescentam-se convenções visuais, padrões de interface e
práticas de sistema que evoluíram no ofício.

O horizonte de 2027 não muda os fundamentos — muda a escala. Produtos são
montados por times distribuídos, a partir de bibliotecas de componentes, com
assistência de sistemas automáticos que geram e recombinam interface. Nesse
cenário, o valor de diretrizes claras aumenta: elas são o contrato que mantém
coerência quando muitas mãos — humanas e não — tocam o mesmo produto.

# Como usar este livro

Este não é um livro para ser lido de capa a capa numa sentada, embora possa
ser. É um livro de consulta. Cada capítulo é autossuficiente e segue a mesma
estrutura:

- **Princípio** — a ideia central, enunciada em uma frase.
- **Por quê** — o fundamento perceptivo, cognitivo ou prático.
- **Diretrizes** — recomendações acionáveis, numeradas.
- **Faça / Evite** — o contraste rápido para revisão.
- **Nota de aplicação** — como a diretriz muda conforme o contexto.

Quando um ponto se apoia em uma fonte específica da literatura de design, a
fonte é nomeada no texto e listada na **Bibliografia** (Apêndice C). Quando o
texto sintetiza, argumenta ou generaliza além de uma fonte pontual, isso é
raciocínio de projeto — legítimo, e sinalizado pela ausência de atribuição.

Uma convenção de leitura: onde se lê **"a interface"**, entenda qualquer
superfície com que a pessoa interage — web, aplicativo, painel, dispositivo.
Os princípios são os mesmos; muda o material.

---

# Parte I — Fundamentos

Antes de qualquer decisão visual, três verdades governam tudo o que vem depois:
o que caracteriza um bom design, como as pessoas percebem e decidem, e como uma
interface comunica o que é possível fazer. Esta parte estabelece esse chão.

## Capítulo 1 — O que é bom design

**Princípio.** Bom design é aquilo que torna a intenção da pessoa realizável com
o mínimo de atrito, sem que ela precise pensar na interface.

**Por quê.** A interface é meio, nunca fim. Ninguém abre um produto para admirar
seus botões; abre para resolver algo. Quanto mais a interface se faz notar —
por confusão, por enfeite, por inconsistência — mais ela rouba atenção da tarefa
real. O melhor design frequentemente é o que desaparece.

Isso não é subjetivo. Há qualidades recorrentes em interfaces que funcionam, e
elas podem ser enunciadas como critérios.

**Diretrizes.**

1. **Clareza acima de tudo.** Se a pessoa não entende o que está vendo ou o que
   fazer, nenhuma outra qualidade importa. Priorize entendimento sobre
   originalidade.
2. **Consistência interna.** O mesmo elemento deve significar a mesma coisa em
   todo o produto. Um botão primário, um estado de erro, um espaçamento — uma
   vez definidos, repetem-se. Inconsistência obriga a pessoa a reaprender a
   cada tela.
3. **Hierarquia deliberada.** Nem tudo pode ter o mesmo peso. Decida o que é
   primário, secundário e terciário, e faça a forma refletir essa decisão
   (Capítulo 7).
4. **Feedback imediato.** Toda ação da pessoa merece uma resposta perceptível do
   sistema (Capítulo 3). Silêncio é ambiguidade.
5. **Tolerância a erro.** Presuma que a pessoa errará. Torne erros difíceis de
   cometer, fáceis de perceber e simples de desfazer.
6. **Economia.** Cada elemento adicionado cobra um preço de atenção. Adicione
   apenas o que ganha o próprio espaço; remova o resto.

**Faça / Evite.**

- **Faça** enunciar, para cada tela, qual é a única tarefa mais importante.
- **Faça** justificar cada elemento pela tarefa que serve.
- **Evite** originalidade que custa entendimento.
- **Evite** decorar o que deveria ser removido.

**Nota de aplicação.** "Mínimo de atrito" não significa mínimo de passos a
qualquer custo. Uma confirmação a mais antes de uma ação destrutiva é atrito
que protege. O objetivo é remover o atrito gratuito — não todo atrito.

## Capítulo 2 — Como as pessoas percebem e decidem

**Princípio.** O comportamento diante de uma interface é previsível porque a
percepção e a decisão humanas seguem regularidades mensuráveis. Projete com
elas, não contra.

**Por quê.** Boa parte do que sentimos como "intuição de design" é, na verdade,
conhecimento consolidado sobre a cognição humana. Como registra a literatura de
interação humano-computador, modelos como a **lei de Fitts** e a **lei de Hick**
tornaram-se pilares da pesquisa em IHC ao longo de décadas, enraizados na teoria
da informação e na psicologia experimental — a lei de Fitts descrevendo o
controle motor, a de Hick, a tomada de decisão. Projetar ignorando-as é apostar
contra evidência acumulada.

**As regularidades que mais importam na prática.**

- **Lei de Fitts (alvo e distância).** O tempo para atingir um alvo cresce com a
  distância e diminui com o tamanho do alvo. Consequência direta: elementos
  importantes e frequentes devem ser grandes e próximos de onde a mão ou o
  cursor já está. Alvos de toque minúsculos são uma violação cotidiana desta
  lei.
- **Lei de Hick (número de opções).** O tempo de decisão cresce com a quantidade
  e a complexidade das opções. Consequência: menus longos, telas com dezenas de
  escolhas equivalentes e formulários intermináveis não são apenas feios — são
  lentos. Agrupe, priorize e revele progressivamente.
- **Limite da memória de trabalho (agrupamento).** As pessoas retêm poucos itens
  simultaneamente. Divida informação e controles em grupos pequenos e
  significativos; não peça que a pessoa carregue estado na cabeça de uma tela
  para outra.
- **Carga cognitiva.** Todo elemento que a pessoa precisa interpretar consome um
  orçamento finito de atenção. A literatura de comunicação visual é explícita:
  excesso de pontos "dilui a mensagem central e ainda aumenta a carga cognitiva
  da audiência". Reduzir elementos é reduzir esforço de compreensão.

**Diretrizes.**

1. **Aumente e aproxime o que importa.** Dimensione alvos pela sua importância e
   frequência de uso, não por simetria estética.
2. **Reduza escolhas simultâneas.** Prefira poucas opções bem hierarquizadas a
   muitas opções planas. Use revelação progressiva para adiar complexidade.
3. **Agrupe em blocos de sete ou menos.** Quebre listas e formulários longos em
   seções curtas e rotuladas.
4. **Corte antes de acrescentar.** Ao revisar uma tela, a primeira pergunta é o
   que pode sair — não o que falta.

**Faça / Evite.**

- **Faça** medir a distância entre a ação frequente e o ponto de partida do
  cursor/dedo.
- **Faça** contar as escolhas simultâneas em cada decisão que você pede.
- **Evite** menus planos e longos quando um agrupamento resolveria.
- **Evite** telas que exigem lembrar o que estava na tela anterior.

**Nota de aplicação.** Essas leis descrevem tendências, não destinos. Uma pessoa
motivada supera uma interface ruim; uma pessoa apressada abandona uma interface
boa. As leis dizem para onde a probabilidade pende — e, no agregado de milhares
de sessões, a probabilidade vence.

## Capítulo 3 — Affordances, sinais e feedback

**Princípio.** Uma interface deve comunicar, pela própria forma, o que é
possível fazer e o que aconteceu depois de agir.

**Por quê.** *Affordance* é a qualidade de um elemento que sugere seu uso — uma
alça convida a puxar, um botão elevado convida a pressionar. Quando a forma não
condiz com a função, surgem as *falsas affordances*: algo que parece clicável e
não é, ou o contrário. A pesquisa em interação é direta sobre o custo disso:
"tapabilidade pobre pode levar à falta de descoberta e a falsas affordances que
geram frustração, incerteza e erros" no uso. A percepção do que é interativo
depende de sinais visuais aprendidos — e a discussão sobre estilos como o
neumórfico gira justamente em torno de quanta affordance de clique um botão
"plano" ainda comunica.

Se a *affordance* diz "isto é possível", o **feedback** diz "isto aconteceu".
Sem resposta, a pessoa não sabe se o toque registrou, se o sistema está
processando ou se algo falhou — e a ambiguidade produz toques repetidos, recuos
e desconfiança.

**Diretrizes.**

1. **Faça o interativo parecer interativo.** Botões, links e campos devem ter
   sinais visuais consistentes que os distingam de conteúdo estático. Reserve
   esses sinais apenas para o que de fato responde ao toque.
2. **Dimensione alvos de toque para o dedo, não para o cursor.** Alvos pequenos
   demais são a falha de affordance mais comum em telas de toque.
3. **Responda a toda ação.** Estado de foco, de pressionado, de carregando, de
   sucesso e de erro — cada um com um sinal perceptível e imediato.
4. **Torne o estado do sistema visível.** A pessoa deve sempre poder responder:
   onde estou, o que está acontecendo, o que posso fazer agora.
5. **Diferencie os estados.** Desabilitado, vazio, carregando e com erro são
   estados distintos e devem parecer distintos — nunca o mesmo cinza mudo.

**Faça / Evite.**

- **Faça** um inventário de estados para cada componente interativo.
- **Faça** o feedback de erro dizer o que aconteceu e como corrigir.
- **Evite** elementos que parecem clicáveis mas não são (e vice-versa).
- **Evite** ações sem confirmação visível de que ocorreram.

**Nota de aplicação.** Feedback não precisa ser barulhento. Uma mudança sutil de
estado costuma bastar; animações longas e sons atrapalham mais do que ajudam. A
regra é ser perceptível, não chamativo.

---

# Parte II — Linguagem visual

Definidos os fundamentos, entra a matéria-prima da interface: letra, cor, grade
e espaço. Estes quatro capítulos tratam de como o visível carrega significado —
e de como manter esse significado consistente.

## Capítulo 4 — Tipografia

**Princípio.** O texto é a maior parte de quase toda interface; a tipografia é,
portanto, a decisão visual de maior alcance. Priorize legibilidade e ritmo antes
de personalidade.

**Por quê.** Antes de ler o conteúdo, a pessoa lê a forma. Corpo pequeno demais,
linhas longas demais, entrelinhas apertadas — cada um cobra esforço a cada
parágrafo. A tradição do desenho de tipos, documentada em obras de referência
sobre desenho de letras, insiste num ponto que atravessa séculos: num tipo bem
desenhado há diferença deliberada entre os traços principais e secundários e
modelagem nas curvas — a qualidade nasce do detalhe controlado, não do enfeite.
O que vale para a letra vale para a página: qualidade tipográfica é disciplina,
não ornamento.

**Diretrizes.**

1. **Escala tipográfica limitada.** Defina poucos tamanhos (por exemplo, cinco a
   sete degraus) e use-os com rigor. Muitos tamanhos destroem hierarquia.
2. **Medida de linha confortável.** Busque cerca de 45 a 75 caracteres por
   linha no texto corrido. Linhas longas cansam; linhas curtíssimas fragmentam a
   leitura.
3. **Entrelinha proporcional ao corpo e à medida.** Texto corrido pede
   entrelinha generosa; títulos, mais apertada. Ajuste ao tamanho e à largura.
4. **Contraste de peso, não de família.** Hierarquia tipográfica limpa costuma
   vir de variar peso e tamanho dentro de uma ou duas famílias — não de misturar
   muitas fontes.
5. **Corpo mínimo legível.** Não sacrifique o corpo do texto para caber mais na
   tela. Densidade que impede leitura não é densidade — é ruído.
6. **Alinhamento a serviço da leitura.** Prefira alinhamento à esquerda para
   blocos longos; reserve centralizado para trechos curtos.

**Faça / Evite.**

- **Faça** definir uma escala tipográfica e tratá-la como lei do produto.
- **Faça** testar legibilidade no menor tamanho e no pior contraste reais.
- **Evite** mais de duas famílias sem razão funcional.
- **Evite** linhas que ultrapassam a medida confortável.

**Nota de aplicação.** A personalidade tipográfica é real e importa para marca —
mas entra depois da legibilidade, nunca no lugar dela. Um tipo cheio de caráter
que ninguém lê confortavelmente falhou na sua função primeira.

## Capítulo 5 — Cor

**Princípio.** Cor é informação antes de ser estética. Cada cor no produto deve
ter um trabalho; cor sem trabalho é ruído.

**Por quê.** A cor é o elemento visual de percepção mais imediata — captura
atenção antes de a pessoa ler qualquer palavra. Por isso é perigosa quando
usada por decoração: um sistema em que tudo é colorido não destaca nada. A
prática de comunicação visual resume o uso correto em uma frase: "a cor é usada
de forma consistente e intencional para destacar informação importante".
Consistência e intenção são as duas exigências; o restante é preferência.

A cor também é notoriamente difícil de dominar — um "conceito escorregadio",
como reconhece a literatura clássica sobre mistura de cores —, e é por isso que
convém tratá-la como sistema, não como escolha caso a caso.

**Diretrizes.**

1. **Paleta pequena e nomeada por função.** Defina cores por papel (primária de
   ação, superfície, texto, sucesso, alerta, erro), não por gosto. Menos cores,
   usadas com regra, comunicam mais.
2. **Nunca use a cor sozinha para comunicar significado.** Estado, seleção e
   erro precisam de um segundo sinal (ícone, texto, forma). Parte da audiência
   não distingue certas cores.
3. **Contraste suficiente entre texto e fundo.** Legibilidade é requisito, não
   ajuste fino. Verifique o contraste em condições reais (Capítulo 16).
4. **Reserve a cor de maior saliência para a ação mais importante.** Se tudo
   grita, nada é ouvido; guarde o acento para o destaque que merece atenção.
5. **Cor consistente entre temas.** Em modos claro e escuro, o significado da
   cor deve permanecer o mesmo; mudam os valores, não os papéis.

**Faça / Evite.**

- **Faça** listar o papel de cada cor antes de escolher os valores.
- **Faça** testar a interface em escala de cinza para checar se a hierarquia
  sobrevive sem cor.
- **Evite** comunicar erro ou sucesso apenas por vermelho ou verde.
- **Evite** paletas que colorem tudo e destacam nada.

**Nota de aplicação.** O teste do cinza é revelador: se a interface deixa de
funcionar sem cor, ela dependia da cor para fazer um trabalho que a hierarquia
deveria fazer. Cor deve reforçar a estrutura, não substituí-la.

## Capítulo 6 — Grid, layout e composição

**Princípio.** O grid é a estrutura invisível que dá ordem, ritmo e
previsibilidade ao layout. Ele não limita a criatividade; dá a ela um chão.

**Por quê.** Sem uma malha comum, cada tela é negociada do zero: margens
arbitrárias, alinhamentos que quase batem, espaçamentos que variam sem razão. O
resultado é uma sensação difusa de desleixo, mesmo quando cada peça é bonita. A
tradição do design gráfico — de manuais de grids e de obras sobre "construir e
romper a grade" — trata o sistema de grid como a ferramenta primária de
composição: é ele que permite alinhar, repetir e criar hierarquia com
consistência. Uma técnica clássica, herdada da comunicação visual, é dividir a
área em terços com um grid e organizar a composição sobre essas linhas.

**Diretrizes.**

1. **Adote um grid explícito.** Colunas, calhas e margens definidas — e
   respeitadas. Um grid que só existe no arquivo de design, mas não no produto,
   não serve.
2. **Espaçamento por escala, não por olho.** Use uma escala de espaçamento (por
   exemplo, múltiplos de uma unidade base) para margens e distâncias. Números
   arbitrários são a origem mais comum de inconsistência.
3. **Alinhe tudo a algo.** Todo elemento se alinha a uma coluna, a uma linha de
   base ou a outro elemento. "Quase alinhado" lê-se como erro.
4. **Agrupe pela proximidade.** Itens relacionados ficam próximos; itens
   distintos, separados. O espaço é o que agrupa — mais do que caixas e linhas.
5. **Componha para o fluxo de leitura.** Posicione o ponto de entrada e conduza
   o olhar. Layouts sem ponto focal deixam a pessoa sem saber por onde começar.

**Faça / Evite.**

- **Faça** definir grid e escala de espaçamento antes das telas.
- **Faça** revisar o layout perguntando "a que isto está alinhado?".
- **Evite** espaçamentos e margens escolhidos a olho, tela a tela.
- **Evite** romper o grid sem intenção — rompê-lo é uma decisão, não um acidente.

**Nota de aplicação.** Romper a grade é uma ferramenta legítima e poderosa: um
elemento que ultrapassa a coluna ganha ênfase justamente por contrariar a
ordem. Mas isso só funciona quando há uma ordem a contrariar. Primeiro
construa a grade; depois, com propósito, rompa-a.

## Capítulo 7 — Hierarquia visual e espaço

**Princípio.** Hierarquia visual é o mapa que diz ao olho a ordem de leitura.
Espaço em branco é a principal ferramenta para construí-la.

**Por quê.** Toda tela ensina a pessoa, em uma fração de segundo, o que olhar
primeiro. Se a forma não define essa ordem, a pessoa define — mal, e com esforço.
A comunicação visual descreve a função com precisão: a hierarquia visual "dá à
audiência pistas sobre a que informação deve prestar atenção". E decidir a
hierarquia da informação "é importante porque guia o nível de ênfase visual"
que cada parte recebe. Ênfase, portanto, não é enfeite: é a materialização de
uma decisão sobre importância.

O espaço em branco — o vazio deliberado — é o que separa, agrupa e destaca. A
literatura de layout é explícita ao recomendar "criar equilíbrio no layout
usando regras de design e espaço em branco" e ao usá-lo para "dirigir o foco do
leitor à mensagem central". Espaço não é área desperdiçada; é o instrumento que
faz o conteúdo respirar e a hierarquia aparecer.

**Diretrizes.**

1. **Estabeleça três níveis, no máximo.** Primário, secundário, terciário. Mais
   níveis do que isso deixam de ser hierarquia e viram ruído.
2. **Crie ênfase por contraste, não por adição.** Tamanho, peso, cor, espaço e
   posição criam hierarquia. Prefira contrastar poucos elementos a decorar
   muitos.
3. **Use o espaço para agrupar.** Aproxime o relacionado, afaste o distinto.
   Muitas vezes, remover uma borda e ajustar o espaço comunica melhor que
   adicionar uma caixa.
4. **Dê um ponto de entrada.** Cada tela deve ter um elemento que o olho
   encontra primeiro — e a partir do qual o restante se organiza.
5. **Não tenha medo do vazio.** Densidade não é sinônimo de valor. Espaço
   generoso ao redor do que importa é o que faz o que importa ser visto.

**Faça / Evite.**

- **Faça** apertar os olhos diante da tela: o que sobressai deve ser o mais
  importante.
- **Faça** aumentar o espaço antes de aumentar o tamanho.
- **Evite** dar o mesmo peso visual a tudo.
- **Evite** preencher o vazio por desconforto com o vazio.

**Nota de aplicação.** Hierarquia e espaço são as duas alavancas que resolvem a
maioria dos problemas de "tela confusa" sem adicionar nada — apenas
reorganizando ênfase e respiro. Antes de acrescentar um elemento que "ajude a
guiar", pergunte se reorganizar a hierarquia já não resolveria.

---

# Parte III — Padrões de interface

A linguagem visual se materializa em componentes e fluxos. Esta parte trata do
vocabulário concreto da interface: os padrões reutilizáveis, o caso especial dos
formulários e a estrutura que organiza tudo — a navegação.

## Capítulo 8 — Componentes e padrões de UI

**Princípio.** Padrões de interface são soluções já validadas para problemas
recorrentes. Reutilize-os; invente apenas onde o problema é genuinamente novo.

**Por quê.** A pessoa que usa seu produto usa centenas de outros. Ela chega com
um vocabulário aprendido: sabe o que um campo de busca faz, como um menu se
comporta, o que esperar de um botão primário. Padrões de UI capitalizam esse
conhecimento prévio — a literatura de padrões de interface existe justamente
para catalogar essas soluções e as *affordances* que as tornam legíveis.
Reinventar um padrão consolidado obriga a pessoa a reaprender o óbvio, gasta seu
orçamento de atenção com a mecânica e não com a tarefa.

**Diretrizes.**

1. **Prefira o padrão conhecido ao original.** Se existe uma convenção para o
   problema, adote-a. Originalidade em mecânica básica raramente compensa o custo
   de reaprendizado.
2. **Um componente, um comportamento.** O mesmo componente deve se comportar
   igual em todo lugar. Variações silenciosas de comportamento minam a confiança.
3. **Projete o componente inteiro, com todos os estados.** Padrão (repouso),
   foco, pressionado, carregando, desabilitado, vazio, erro. Um componente
   "pronto" que só tem o estado feliz não está pronto.
4. **Componha a partir de peças pequenas.** Componentes menores e combináveis
   envelhecem melhor que componentes grandes e específicos.
5. **Nomeie os padrões.** Um vocabulário compartilhado ("card", "chip",
   "banner de estado") acelera a comunicação do time e reduz divergência.

**Faça / Evite.**

- **Faça** catalogar os padrões que seu produto já usa antes de criar mais um.
- **Faça** especificar todos os estados de cada componente.
- **Evite** recriar do zero o que já é convenção.
- **Evite** o mesmo componente com comportamentos diferentes em telas diferentes.

**Nota de aplicação.** Padrões são ponto de partida, não camisa de força. Há
casos em que o problema do seu produto é realmente diferente — e aí a invenção
se justifica. A disciplina é reservar a invenção para onde ela agrega, não
gastá-la na mecânica que a pessoa já domina.

## Capítulo 9 — Formulários

**Princípio.** Formulário é conversa: o produto pergunta, a pessoa responde. Bom
design de formulário reduz o número, o esforço e o risco de cada pergunta.

**Por quê.** O formulário é onde a interface mais exige — atenção, dados,
paciência — e, portanto, onde mais gente desiste. Cada campo é uma pergunta que
cobra esforço; cada erro mal comunicado é um motivo para abandonar. A literatura
dedicada a padrões de formulário trata o tema como disciplina própria justamente
porque pequenos detalhes (rótulos, ordem, mensagens de erro, momento da
validação) decidem entre conclusão e desistência.

**Diretrizes.**

1. **Peça o mínimo.** Cada campo precisa justificar sua existência. Campo que
   pode ser inferido, adiado ou eliminado não deve estar ali.
2. **Um rótulo claro e sempre visível.** Rótulos que somem quando a pessoa
   digita sobrecarregam a memória. Prefira rótulos persistentes a placeholders
   que fazem as vezes de rótulo.
3. **Ordem lógica, agrupada.** Organize os campos na sequência natural da tarefa
   e agrupe os relacionados. Formulários longos pedem seções curtas e rotuladas
   (Capítulo 2).
4. **Valide no momento certo e explique o erro.** Sinalize o problema perto do
   campo, diga o que aconteceu e como corrigir. Erro genérico no topo, longe do
   campo, é frustração garantida.
5. **Preserve o trabalho da pessoa.** Nunca limpe um formulário por causa de um
   erro. Mantenha o que foi digitado e aponte apenas o que precisa mudar.
6. **Estados de envio explícitos.** Mostre carregando, sucesso e falha sem
   ambiguidade. A pessoa precisa saber se o envio ocorreu.

**Faça / Evite.**

- **Faça** contar os campos e cortar os dispensáveis antes de desenhar.
- **Faça** posicionar a mensagem de erro junto ao campo que a causou.
- **Evite** placeholders no lugar de rótulos.
- **Evite** apagar dados já digitados ao reportar um erro.

**Nota de aplicação.** O melhor formulário é o que não existe. Antes de desenhar
campos, pergunte se o dado é mesmo necessário agora, ou se pode ser obtido de
outra forma, mais tarde, ou nunca. Reduzir a conversa vale mais que embelezá-la.

## Capítulo 10 — Navegação e arquitetura da informação

**Princípio.** Navegação é a resposta contínua a três perguntas: onde estou,
para onde posso ir, como volto. A arquitetura da informação é a estrutura que
torna essas respostas possíveis.

**Por quê.** Se a pessoa não sabe onde está, não consegue decidir o próximo
passo — e a desorientação é uma das causas mais silenciosas de abandono. A
comunicação visual descreve a navegação como a construção de "um caminho" pelo
conteúdo, com "pistas visuais" que sinalizam cada transição. O mesmo vale para
qualquer interface: a estrutura precisa oferecer um percurso legível, e cada
transição precisa de um sinal claro. Quando os caminhos competem, "a pessoa fica
sem saber onde focar e como navegar".

**Diretrizes.**

1. **Torne a localização sempre visível.** Título de tela, item ativo destacado,
   trilha de navegação quando a profundidade justifica. A pessoa nunca deve
   precisar adivinhar onde está.
2. **Rotule por linguagem da pessoa, não do sistema.** Nomes de seção descrevem
   o conteúdo do ponto de vista de quem procura — não da estrutura interna do
   produto.
3. **Ofereça sempre a volta.** Todo avanço tem um retorno óbvio. Becos sem saída
   corroem confiança.
4. **Agrupe por tarefa, não por organograma.** Estruture a informação em torno do
   que as pessoas vêm fazer, não da forma como o time é dividido.
5. **Poucas escolhas primárias.** Uma navegação principal enxuta, com revelação
   progressiva do resto, decide mais rápido que um menu que mostra tudo de uma
   vez (Capítulo 2).
6. **Consistência de posição e comportamento.** Navegação que muda de lugar ou de
   regra entre telas obriga a reaprender o produto a cada passo.

**Faça / Evite.**

- **Faça** garantir, em cada tela, resposta às três perguntas: onde, para onde,
  como voltar.
- **Faça** validar os rótulos com as palavras que as pessoas realmente usam.
- **Evite** rótulos que descrevem a estrutura interna em vez do conteúdo.
- **Evite** navegação que muda de posição ou comportamento entre telas.

**Nota de aplicação.** Arquitetura da informação é decidida cedo e é cara de
mudar depois. Vale investir em entender como as pessoas agrupam mentalmente o
conteúdo — antes de congelar a estrutura em navegação.

---

# Parte IV — Sistemas de design

Princípios e padrões, para escalar, precisam virar sistema. Esta parte trata de
como transformar decisões de design em ativos reutilizáveis, versionados e
governados — que mantêm coerência quando muitas mãos tocam o mesmo produto.

## Capítulo 11 — O que é um design system

**Princípio.** Um design system é o conjunto compartilhado de padrões e práticas
que mantém um produto coerente à medida que cresce e se ramifica.

**Por quê.** Sem um sistema, cada time reinventa botões, cores e espaçamentos —
e o produto se fragmenta em dialetos visuais que confundem quem usa e desperdiçam
quem constrói. A obra de referência sobre o tema é direta na definição: "um
design system é um conjunto de padrões interconectados e práticas compartilhadas,
organizado de forma coerente". As duas metades importam igualmente: **padrões**
(o que se reutiliza — componentes, estilos, regras) e **práticas** (como o time
decide, documenta e evolui esses padrões). Um sistema é tanto biblioteca quanto
combinado social.

E seu propósito vai além da consistência. Como observa a literatura de sistemas
de design, um dos objetivos centrais é "estender a direção criativa" — permitir
que a intenção de design se propague por muitas telas e muitas mãos sem se
diluir. O sistema não substitui o julgamento; multiplica-o.

**Diretrizes.**

1. **Sistema é padrões + práticas.** Investir só na biblioteca de componentes,
   sem os combinados de como usá-la e evoluí-la, produz um sistema que ninguém
   segue.
2. **Coerência, não uniformidade.** O objetivo é que decisões relacionadas se
   pareçam e se comportem de forma previsível — não que tudo seja idêntico.
3. **Sirva a quem constrói.** Um sistema existe para acelerar e dar segurança ao
   time. Se ele atrasa ou engessa, foi desenhado contra seu próprio propósito.
4. **Um vocabulário compartilhado.** Nomes estáveis para tokens, componentes e
   padrões são a infraestrutura invisível da colaboração.

**Faça / Evite.**

- **Faça** definir tanto os componentes quanto as práticas de uso e evolução.
- **Faça** medir o sistema pela velocidade e confiança que dá ao time.
- **Evite** confundir uniformidade rígida com coerência.
- **Evite** uma biblioteca sem combinados — ela vira só mais um dialeto.

**Nota de aplicação.** Um design system é produto, não projeto: tem pessoas
usuárias (os times), evolui por versões e morre se ninguém o mantém. Trate-o com
o mesmo rigor de qualquer produto — inclusive o de ouvir quem o usa.

## Capítulo 12 — Tokens, componentes e documentação

**Princípio.** Um sistema se sustenta em três camadas: tokens (as decisões
atômicas), componentes (as peças montadas) e documentação (o que torna as duas
primeiras utilizáveis por outros).

**Por quê.** Sem tokens, cada valor de cor, espaçamento e tipografia é digitado à
mão em cada tela — e a deriva é questão de tempo. Sem componentes, cada padrão é
remontado do zero. Sem documentação, o sistema existe apenas na cabeça de quem o
criou, e se perde a cada troca de time. As três camadas convertem intenção de
design em ativo estável e compartilhável.

**Diretrizes.**

1. **Tokens como fonte única de verdade.** Cor, espaçamento, tipografia, raio,
   sombra — cada decisão vira um token nomeado por função e referenciado, nunca
   repetido como valor cru.
2. **Nomeie tokens por papel, não por aparência.** "Superfície", "ação
   primária", "texto sutil" sobrevivem a uma mudança de marca; "azul-500", não.
3. **Componentes especificam comportamento, não só aparência.** A documentação
   de um componente inclui todos os estados, as regras de uso e os antipadrões.
4. **Documente a decisão, não apenas o resultado.** Registrar por que uma regra
   existe evita que ela seja quebrada por desconhecimento e permite revisá-la
   com contexto.
5. **A documentação vive ao lado do código.** Documentação que diverge do que o
   produto faz é pior que documentação nenhuma — ela mente com autoridade.

**Faça / Evite.**

- **Faça** derivar todos os valores da interface de tokens nomeados.
- **Faça** documentar estados, regras e antipadrões de cada componente.
- **Evite** valores crus repetidos pelas telas.
- **Evite** documentação que descreve um ideal que o produto não cumpre.

**Nota de aplicação.** A prova de fogo dos tokens é a mudança: trocar a marca, o
tema ou a densidade deveria ser alterar um conjunto pequeno de tokens — não
caçar valores por centenas de telas. Se a mudança é cara, os tokens não estavam
fazendo seu trabalho.

## Capítulo 13 — DesignOps e governança

**Princípio.** Um sistema de design é tão bom quanto o processo que o mantém.
DesignOps é a disciplina de operar design em escala — e a governança, o conjunto
de regras que decide como o sistema muda.

**Por quê.** Sistemas não se degradam por falta de talento, mas por falta de
operação: contribuições sem revisão, decisões sem dono, versões sem
comunicação. À medida que mais times dependem do sistema, o custo de uma mudança
mal governada cresce — e o de uma mudança bloqueada, também. DesignOps existe
para que o sistema evolua sem quebrar quem depende dele.

**Diretrizes.**

1. **Defina como uma mudança entra.** Quem propõe, quem revisa, quem aprova, como
   se comunica. Sem esse caminho, cada mudança é uma negociação improvisada.
2. **Versione e comunique.** Mudanças que quebram compatibilidade precisam de
   aviso, de caminho de migração e de tempo. Surpresas corroem a confiança no
   sistema.
3. **Meça adoção, não só cobertura.** Um sistema completo que ninguém usa
   fracassou. Acompanhe onde o sistema é adotado e onde é contornado — e por quê.
4. **Equilibre estabilidade e evolução.** Rígido demais, o sistema é abandonado;
   frouxo demais, deixa de ser sistema. Governança é o ajuste contínuo desse
   equilíbrio.
5. **Feche o ciclo com quem usa.** Contribuições e atritos dos times são o melhor
   sinal do que o sistema precisa. Trate-os como pauta, não como ruído.

**Faça / Evite.**

- **Faça** documentar o caminho de contribuição e de aprovação.
- **Faça** acompanhar adoção real e casos de contorno.
- **Evite** mudanças que quebram sem aviso e sem migração.
- **Evite** medir sucesso apenas pelo tamanho da biblioteca.

**Nota de aplicação.** Onde os times contornam o sistema, não está a
indisciplina — está a informação. Cada contorno aponta uma lacuna ou um atrito
que o sistema ainda não resolveu. Governança madura escuta os contornos antes de
proibi-los.

---

# Parte V — Dados e comunicação visual

Nem toda interface pede ação; muita interface pede compreensão. Esta parte trata
de comunicar informação com precisão e honestidade — em painéis que se lê todo
dia e em peças que contam uma história de uma vez.

## Capítulo 14 — Visualização de dados e dashboards

**Princípio.** Um bom gráfico responde a uma pergunta antes que a pessoa termine
de formulá-la. Um bom painel responde às perguntas certas, na ordem certa, sem
ruído.

**Por quê.** Dados não falam sozinhos; a forma escolhida para mostrá-los decide o
que será compreendido — e o que será distorcido. A percepção visual segue
regularidades (agrupamento por proximidade e semelhança, saliência do que
contrasta) que a boa visualização usa a favor da leitura e a má usa contra. Um
painel é, por definição, denso; por isso a disciplina da hierarquia e do espaço
(Capítulo 7) é ainda mais crítica aqui do que em qualquer outra tela.

**Diretrizes.**

1. **Uma pergunta por gráfico.** Cada visualização deve ter uma pergunta clara
   que responde. Gráfico sem pergunta é decoração de dados.
2. **Escolha a forma pela comparação, não pela beleza.** O tipo de gráfico
   decorre da relação que se quer mostrar (evolução, parte-todo, distribuição,
   correlação) — não da estética.
3. **Remova tudo o que não informa.** Grades pesadas, sombras, terceiras
   dimensões e efeitos gastam tinta sem acrescentar significado. Menos ornamento,
   mais leitura.
4. **Hierarquize o painel.** Nem toda métrica tem a mesma importância. Dê ao
   olho um ponto de entrada e uma ordem de leitura; não alinhe tudo num mosaico
   plano.
5. **A cor carrega significado, não enfeite.** Reserve cor para codificar
   categoria ou destacar exceção; padronize o significado de cada cor em todo o
   painel (Capítulo 5).
6. **Diga a incerteza.** Dado parcial, defasado ou estimado deve ser sinalizado.
   Precisão aparente onde não há precisão real engana.

**Faça / Evite.**

- **Faça** enunciar a pergunta que cada gráfico responde.
- **Faça** cortar todo elemento gráfico que não mude a leitura.
- **Evite** escolher o tipo de gráfico pela aparência.
- **Evite** exibir como exato o que é estimado.

**Nota de aplicação.** O painel mais valioso raramente é o mais completo — é o
mais focado. Antes de acrescentar mais uma métrica, pergunte que decisão ela
muda. Se nenhuma, ela custa atenção sem pagar informação.

## Capítulo 15 — Infográficos e narrativa visual

**Princípio.** Um infográfico é um argumento visual: conduz a pessoa por uma
sequência que leva a uma mensagem central única.

**Por quê.** Diferente do painel, que responde a muitas perguntas, o infográfico
faz um percurso e conta uma história. A literatura dedicada à criação de
infográficos é explícita sobre os instrumentos desse percurso: hierarquia de
informação para decidir a ênfase, "pistas visuais que criam um caminho de
navegação" pelo conteúdo, e o uso de "espaço em branco" para "criar equilíbrio" e
"dirigir o foco à mensagem central". O risco espelhado também é nomeado: "visuais
demais em competição obscurecem o ponto de entrada e o caminho", deixando a
pessoa perdida. A disciplina é subtrativa — cada elemento a mais aumenta a carga
cognitiva de digerir a mensagem.

**Diretrizes.**

1. **Uma mensagem central.** Tudo no infográfico existe para servir a uma única
   ideia. O que não a serve, sai.
2. **Um caminho de leitura claro.** Defina o ponto de entrada e conduza o olhar
   com pistas visuais consistentes. Sinalize cada transição.
3. **Hierarquia antes de estética.** Decida o que é primário e secundário e faça
   o tamanho, a cor e a posição refletirem isso.
4. **Espaço em branco a serviço do foco.** Use o vazio para separar seções e
   destacar o essencial, não para preencher.
5. **Menos visuais, mais compreensão.** Reduzir o número de elementos reduz a
   carga cognitiva e aumenta a chance de a mensagem ser lembrada.
6. **Credibilidade com fontes.** Uma peça que faz afirmações precisa mostrar de
   onde elas vêm; a origem sustenta a confiança.

**Faça / Evite.**

- **Faça** enunciar em uma frase a mensagem central antes de desenhar.
- **Faça** desenhar o caminho de leitura explicitamente.
- **Evite** competição de visuais que apaga o ponto de entrada.
- **Evite** afirmações sem origem visível.

**Nota de aplicação.** A tentação, num infográfico, é mostrar tudo o que se sabe.
A arte é mostrar só o que sustenta a mensagem — e confiar que o vazio ao redor do
essencial é o que o faz ser visto.

---

# Parte VI — Qualidade, ética e processo

As três últimas exigências não são camadas finais, mas condições que atravessam
todo o resto: que o produto sirva a todas as pessoas, que respeite quem o usa, e
que melhore com base em evidência.

## Capítulo 16 — Acessibilidade

**Princípio.** Acessibilidade é a garantia de que o produto sirva a pessoas com
deficiências visuais, auditivas, motoras ou cognitivas. Não é recurso extra — é
parte da definição de "funciona".

**Por quê.** Uma parcela significativa de qualquer público interage com
tecnologia de forma não padrão: baixa visão, daltonismo, uso apenas de teclado,
leitores de tela, atenção limitada. Interface inacessível não é neutra — ela
exclui. A prática de comunicação visual já incorpora essa exigência ao tornar
peças utilizáveis por "pessoas com deficiências visuais, auditivas ou cognitivas"
(as chamadas exigências de conformidade de acessibilidade). O mesmo padrão vale,
com ainda mais força, para produtos interativos.

**Diretrizes.**

1. **Contraste suficiente sempre.** Texto e elementos essenciais precisam de
   contraste que os torne legíveis para baixa visão. Verifique em condições reais
   — não confie no olho descansado do designer.
2. **Nunca dependa só de cor.** Estado, seleção e erro exigem um segundo sinal
   além da cor (Capítulo 5). Cor sozinha exclui quem não a distingue.
3. **Tudo operável por teclado.** Toda ação possível com o mouse deve ser
   possível com o teclado, em ordem lógica e com foco visível.
4. **Alvos de toque adequados.** Alvos pequenos excluem quem tem controle motor
   reduzido — e prejudicam todo mundo (Capítulo 3).
5. **Texto alternativo e estrutura semântica.** Imagens informativas descrevem
   seu conteúdo; a estrutura da página é legível por leitores de tela. Forma
   visual não pode ser o único portador de significado.
6. **Respeite preferências da pessoa.** Movimento reduzido, tamanho de fonte,
   contraste elevado — quando a pessoa sinaliza uma necessidade, o produto a
   honra.

**Faça / Evite.**

- **Faça** verificar contraste, navegação por teclado e leitura por leitor de
  tela como parte da definição de pronto.
- **Faça** testar com tamanhos de fonte ampliados e em escala de cinza.
- **Evite** comunicar informação essencial apenas pela cor ou pela posição
  visual.
- **Evite** tratar acessibilidade como etapa final, opcional, "se der tempo".

**Nota de aplicação.** Quase toda melhoria de acessibilidade melhora a
experiência de todo mundo: mais contraste, alvos maiores, estrutura mais clara,
menos dependência de um único sentido. Acessibilidade bem feita não é um desvio
do bom design — é bom design levado a sério.

## Capítulo 17 — Design persuasivo e ético

**Princípio.** Interfaces influenciam decisões. A linha ética é clara: ajudar a
pessoa a decidir o que ela quer, nunca enganá-la a decidir o que ela não quer.

**Por quê.** Todo design é persuasivo em algum grau — hierarquia, cor e cópia
guiam escolhas. A referência clássica do campo reconhece isso já no título, ao
falar de "princípios de design para produtos persuasivos e éticos": persuasão e
ética não se opõem, andam juntas. O problema surge quando a persuasão vira
engano. É o território dos **padrões escuros** (*dark patterns*): como define a
literatura de design, um padrão escuro é "um tipo de interface que parece ter
sido cuidadosamente construída para enganar as pessoas a fazerem coisas que não
são do seu interesse". E o alerta é explícito: eles "são muito mais comuns do que
se imagina".

**Diretrizes.**

1. **Alinhe a interface ao interesse da pessoa.** A ação que a forma torna mais
   fácil deve ser a que serve a quem usa — não apenas a que serve à métrica do
   negócio.
2. **Simetria nas escolhas.** Sair deve ser tão fácil quanto entrar; cancelar,
   tão fácil quanto assinar; recusar, tão visível quanto aceitar.
3. **Sem consentimento forçado ou confuso.** Não pré-marque o que retira
   privacidade; não disfarce a recusa; não esconda o custo. Consentimento obtido
   por confusão não é consentimento.
4. **Sem urgência e escassez falsas.** Contadores fabricados e "restam poucos"
   inventados manipulam pela mentira. A pressão só é legítima se for verdadeira.
5. **Transparência sobre custo e compromisso.** Preço, recorrência e
   permanência devem ser visíveis antes da decisão, não descobertos depois.

**Faça / Evite.**

- **Faça** perguntar, de cada fluxo: se a pessoa entendesse tudo, ainda
  escolheria isto?
- **Faça** tornar o cancelamento e a recusa tão simples quanto a adesão.
- **Evite** o padrão escuro — mesmo quando ele "converte".
- **Evite** urgência, escassez ou consentimento fabricados.

**Nota de aplicação.** O teste ético mais simples é o da luz do dia: você
explicaria essa escolha de design, em voz alta, para a pessoa afetada por ela? Se
a resposta depende de que ela não perceba, o design é um padrão escuro — por mais
que os números o recompensem no curto prazo.

## Capítulo 18 — Pesquisa, testes e melhoria contínua

**Princípio.** Nenhuma decisão de design é definitiva até encontrar quem usa. A
opinião do time é hipótese; o comportamento das pessoas é evidência.

**Por quê.** Designers não são a audiência — conhecem demais o produto,
raciocinam pela estrutura interna, não percebem o que confunde. Por isso a
prática madura de design é empírica: envolve as pessoas cedo e itera. A pesquisa
em interação descreve o método com clareza — "decisões-chave de design"
informadas por "oficinas de co-design e testes de usabilidade iterativos" — e o
teste de usabilidade, inclusive com técnicas como rastreamento ocular, é usado
para avaliar "eficiência, confiança e apelo visual" de uma interface. Medir o
comportamento real substitui a discussão de opiniões por evidência.

**Diretrizes.**

1. **Teste cedo, com pouco.** Poucas pessoas, um protótipo tosco e uma tarefa
   real revelam mais problemas do que meses de debate interno. O custo de testar
   cedo é baixo; o de errar tarde, alto.
2. **Observe a tarefa, não peça opinião.** Dê à pessoa uma tarefa e observe onde
   ela hesita, erra ou desiste. O que as pessoas fazem informa mais do que o que
   dizem preferir.
3. **Separe evidência de interpretação.** Registre o que foi observado antes de
   explicar por quê. Conclusões apressadas contaminam o dado.
4. **Itere em ciclos curtos.** Projete, teste, aprenda, ajuste — e repita.
   Design é um processo de aproximação, não um ato de acerto único.
5. **Feche o ciclo depois do lançamento.** Comportamento em produção é a maior
   amostra de teste que existe. Instrumente, observe e continue ajustando.
6. **Trate feedback como matéria-prima, não veredito.** Cada retorno — de teste,
   de suporte, de métrica — é um sinal a interpretar, não uma ordem a cumprir
   cegamente nem um ruído a ignorar.

**Faça / Evite.**

- **Faça** validar decisões importantes com pessoas reais, antes de escalar.
- **Faça** observar tarefas reais em vez de coletar preferências abstratas.
- **Evite** confundir a familiaridade do time com a clareza para quem chega.
- **Evite** tratar a primeira solução como final sem confrontá-la com uso real.

**Nota de aplicação.** A pergunta que encerra qualquer projeto não é "gostamos
disto?", mas "as pessoas conseguem fazer o que vieram fazer?". A primeira é sobre
o time; a segunda, sobre o produto. Só a segunda importa.

---

# Apêndice A — Checklists de bolso

**Antes de fechar uma tela**
- [ ] Qual é a única tarefa mais importante desta tela?
- [ ] O que o olho encontra primeiro é o mais importante?
- [ ] Todo elemento se justifica pela tarefa? O que pode sair?
- [ ] Cada elemento se alinha a algo (coluna, linha de base, outro elemento)?
- [ ] Espaçamentos vêm da escala, não do olho?
- [ ] Toda ação tem feedback perceptível?
- [ ] A interface sobrevive em escala de cinza?
- [ ] Contraste, teclado e leitor de tela verificados?

**Antes de fechar um componente**
- [ ] Todos os estados: repouso, foco, pressionado, carregando, desabilitado,
      vazio, erro?
- [ ] Comportamento idêntico em todo o produto?
- [ ] Valores derivados de tokens, não crus?
- [ ] Nome estável e documentado?
- [ ] Regras de uso e antipadrões registrados?

**Antes de fechar um formulário**
- [ ] Cada campo é necessário agora?
- [ ] Rótulos claros e persistentes?
- [ ] Erros junto ao campo, explicando causa e correção?
- [ ] O trabalho da pessoa é preservado em caso de erro?
- [ ] Estados de envio (carregando, sucesso, falha) explícitos?

**Antes de lançar um fluxo (teste ético)**
- [ ] Sair é tão fácil quanto entrar?
- [ ] Consentimento é claro, não forçado nem confuso?
- [ ] Alguma urgência ou escassez é fabricada?
- [ ] Você explicaria cada escolha, em voz alta, à pessoa afetada?

# Apêndice B — Glossário

- **Affordance** — qualidade de um elemento que sugere, pela forma, como usá-lo.
- **Antipadrão** — solução comum que parece resolver um problema mas o agrava.
- **Arquitetura da informação** — estrutura que organiza e rotula o conteúdo para
  torná-lo encontrável.
- **Carga cognitiva** — esforço mental exigido para compreender e usar algo.
- **Design system** — conjunto de padrões interconectados e práticas
  compartilhadas que mantém um produto coerente.
- **DesignOps** — disciplina de operar e escalar design (processo, ferramentas,
  governança).
- **Espaço em branco** — área deliberadamente vazia usada para separar, agrupar e
  destacar.
- **Grid** — malha de colunas, calhas e margens que estrutura o layout.
- **Hierarquia visual** — ordenação da forma que indica a sequência de leitura e
  a importância relativa.
- **Lei de Fitts** — o tempo para atingir um alvo cresce com a distância e cai
  com o tamanho.
- **Lei de Hick** — o tempo de decisão cresce com a quantidade e a complexidade
  das opções.
- **Padrão escuro (dark pattern)** — interface construída para enganar a pessoa a
  agir contra o próprio interesse.
- **Padrão de UI** — solução reutilizável e reconhecível para um problema
  recorrente de interface.
- **Revelação progressiva** — técnica de adiar complexidade, mostrando o avançado
  apenas quando pedido.
- **Token de design** — decisão atômica (cor, espaçamento, tipografia) nomeada por
  função e referenciada, não repetida como valor cru.

# Apêndice C — Bibliografia

Obras de referência que fundamentam as diretrizes deste livro:

**Fundamentos, princípios e interação**
- *Laws of UX — Design Principles for Persuasive and Ethical Products.*
- *Universal Principles of UX.*
- *Design Concepts and Principles* (referência compilada).
- *Artificial Intelligence for Human-Computer Interaction.*
- *Object-Oriented Software Engineering* (fundamentos de interação).

**Padrões de interface e formulários**
- *Designing Better UX with UI Patterns.*
- *Web UI Design Best Practices.*
- *Form Design Patterns.*

**Linguagem visual: tipografia, cor, grid e layout**
- *Color: A Course in Mastering the Art of Mixing Colors.*
- *Best Practices for Graphic Designers: Grids and Page Layouts.*
- *Basics Design: Layout.*
- *Making and Breaking the Grid — A Graphic Design Layout.*
- *Graphic Design and Print Production Fundamentals.*
- *Walter Tracy — Letters of Credit: A View of Type Design.*
- *Archigraphy: Lettering on Buildings.*

**Sistemas de design**
- *Design Systems: A Practical Guide to Creating Design Systems.*
- *DesignOps Guide 101.*
- *Designing in Figma.*

**Dados e comunicação visual**
- *10 Steps to Creating an Infographic — A Practical Guide.*
- *Dashboard Design.*
- *Data Viz 105: Introduction to Infographics.*
- *Creating Infographics with Adobe Illustrator.*

---

*Diretrizes de Design 2027 — Edição em Português (Brasil).*
