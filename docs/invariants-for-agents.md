---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-07-26"
---

# Invariants for Agents

### Six lenses from a mathematics shelf on the engineering of autonomous systems

**Epistemic status.** Every claim below about a cited paper is drawn from that
paper's abstract and introduction and is attributable to it. Every bridge from
mathematics to agent engineering is an **analogy** — an interpretive move, not a
theorem — and is labeled as such where it occurs. Nothing here should be read as
a formal result about AI systems; the point is that the *shape* of certain
mathematical arguments is a better design vocabulary for agents than the
folklore we currently use.

---

## 1. Two shelves, one question

Put two stacks of recent preprints side by side. On the first: an obstruction
theory for Dirac complements in Courant algebroids [1]; a decidability proof for
branching vector addition systems via semilinear inductive invariants [2]; a
sharp probabilistic threshold for recognizing supersoluble groups [3]; the
resolution of Rump's retraction conjecture for quasilinear cycle sets [4]; a
robustness and backstepping theory for stochastic differential equations [5];
and a triangulation of the permutahedron that connects two classifying spaces of
the same Artin group [6].

On the second stack: an economic model of when organizations replace human labor
with AI [7]; an argument that some human participation in automated work is
constitutive rather than provisional [8]; an experimental dissection of how
language models yield to or resist social pressure [9]; a framework that treats
agent contexts as evolving playbooks rather than compressed summaries [10]; and
an empirical study in which a general code agent, wrapped in a verification
harness, proves every one of 4,257 separation-logic lemmas it is pointed at
[11].

The two stacks are usually read by different people. The wager of this essay is
that the first stack contains, in disguised form, the missing engineering
discipline of the second. Agent engineering today is largely pre-mathematical:
we speak of "guardrails," "evals," "context management," and "human in the
loop" the way pre-Newtonian mechanics spoke of impetus — suggestive words
awaiting structure. The mathematics shelf does not hand us theorems about
agents. It hands us something more transferable: *argument shapes*. A
certificate of impossibility. An obstruction class. A sharp recognizability
threshold. A retraction. A Lyapunov function. An explicit homotopy equivalence.
Each of these is a way of *making a claim inspectable*, and inspectability is
precisely what agentic systems lack.

One clarification before starting: throughout, "the agent" means an LLM-driven
system that acts — plans, calls tools, edits state — and whose outputs are, by
the statistical nature of the model class, unverified by default. That premise
is not pessimism; it is the operating assumption under which every serious
harness in the second stack is built [11].

---

## 2. Witnesses and invariants

Bizière, Leroux, and Sutre close a problem open for more than thirty years: the
reachability problem for branching vector addition systems (BVAS) is decidable
[2]. The proof mechanism matters more, for our purposes, than the result. They
show that whenever a configuration is *not* reachable, there exists a
**semilinear inductive invariant** — a Presburger-definable set that contains
the initial configurations, is closed under every action of the system, and
excludes the target. Decidability then falls out of a beautifully naive
procedure: run two enumerations in parallel, one searching for an execution
(a witness that the answer is "yes"), one searching for a semilinear invariant
excluding the target (a certificate that the answer is "no"). One of the two
must terminate.

The symmetry deserves to be stated plainly, because it is the deepest single
idea either shelf has to offer:

> A positive claim is certified by a **witness** — a concrete execution you can
> replay. A negative claim is certified by an **invariant** — a set, closed
> under the system's dynamics, that separates what is from what cannot be.

**Analogy, and a close one.** An agent harness is exactly this two-sided
machine. When an agent claims success, the claim is worthless until it is a
witness: a diff that applies, a test suite that passes, a proof term the
kernel accepts. The Aria study [11] is explicit that its harness accepts a
proof only when the prover's kernel closes it — soundness by witness. But the
other side is the one we systematically under-build. A sandbox, a permission
model, a set of forbidden operations — each of these is, structurally, an
*inductive invariant*: a region of state space containing the initial state,
closed under every action the agent is permitted, and excluding the
catastrophic configurations. When we argue that an agent "cannot" exfiltrate a
credential or "cannot" write outside a directory, we are asserting the
existence of such an invariant, usually without exhibiting it. The BVAS result
models the discipline we lack: the safety argument *is* the invariant, and it
must be written down in a decidable language, or it is not an argument at all —
it is a mood.

The invariant-based reading also explains why post-hoc review of agent
transcripts is so unsatisfying as a safety mechanism. A transcript is a
witness; witnesses certify what *did* happen. Only invariants certify what
*cannot* happen, and no accumulation of benign transcripts ever sums to an
invariant.

---

## 3. The obstruction to complementarity

Ariel, Rubio, and Zambon ask when a Dirac structure \(L\) inside a Courant
algebroid admits a **Dirac complement** — a second structure \(M\) such that
the ambient object splits cleanly as a double built from the pair [1]. Their
answer has two parts worth carrying across. First, they construct a
*cohomology class whose nonvanishing prevents the existence of a complement*:
an obstruction you can compute before you attempt the splitting. Second, they
prove a striking negative: for a Lie algebra \(\mathfrak{g}\) with a definite
form, the **diagonal** \(\Delta \subset \mathfrak{g} \oplus \bar{\mathfrak{g}}\)
admits no complement unless \(\mathfrak{g}\) is abelian.

**Analogy.** The dream of human–AI collaboration is a complement problem. We
want to split the work: a human structure \(L\), an AI structure \(M\),
together spanning the task, intersecting trivially, each closed under its own
dynamics. The HAT model [7] gives the economic conditions under which the
split happens — cost asymmetry, risk differentials, organizational depth — and
predicts abrupt transitions when its substitution condition flips. But the
Dirac paper contributes the darker, structural half of the story: *some
configurations are obstructed*. The Boundaries-of-Automation argument [8]
identifies exactly such a case, which it calls **target emergence**: activities
whose objective is not specified in advance but constituted by the interaction
itself. In these activities there is no decomposition into "the human part"
and "the AI part," because the target that would define the parts does not
exist until the collaboration produces it. Read structurally: the obstruction
class does not vanish, and no reorganization, however well-funded, will
produce the splitting. An organization that computes this obstruction *before*
redesigning itself — asking "is the target of this role specified
independently of the person performing it?" — is doing cheaply what the
cohomology does rigorously.

And the diagonal result rewards a second look. The diagonal is the subspace of
perfect mirroring — every element paired with itself. It admits no complement
unless the algebra is abelian, i.e., unless nothing interacts. The sycophancy
literature has been circling this exact geometry. The Beyond-Sycophancy
studies [9] show that models drift toward the user's stated position — toward
the diagonal — and that this drift is one face of a general belief-updating
mechanism structured by distance, attributed source, and coalition pressure. A
model sitting on the diagonal contributes nothing that spans beyond its user:
mirroring is precisely the configuration from which complementarity cannot be
built, except in "abelian" settings where no genuine interaction was needed
anyway. That a symplectic-geometry paper and an LLM-psychology paper arrive at
the same moral from opposite directions is the kind of coincidence this essay
exists to point at.

---

## 4. Sharp thresholds, and what a sample can certify

Lucchini proves that if the probability that two random elements of a finite
group generate a supersoluble subgroup is at least \(16/25\), the whole group
is supersoluble — and that the bound is sharp, attained by
\((C_5 \times C_5) \rtimes Q_8\) [3]. This sits in a tradition of
**η-recognizability** results: abelian groups are recognizable at threshold
\(5/8\), nilpotent at \(1/2\), soluble at \(11/30\), each sharp. Two features
of this literature should unsettle anyone who runs model evaluations.

First: the threshold is a cliff, not a gradient, and it is *class-dependent*.
Below \(16/25\), there exist groups passing the two-element test at
substantial rates that are not supersoluble at all; at \(16/25\), membership
is certified. The extremal object is not generic noise but a *specific,
structured counterexample* — a small group with a carefully arranged action.

Second, and worse: some classes are not recognizable from two-generated
subgroups *at any threshold*. The metabelian groups furnish the standard
counterexample [3, citing prior work]: every local sample can lie in the
class while the global object does not.

**Analogy.** An eval suite samples local interactions — prompts, dialogue
pairs, tool-use episodes — and infers a global property of the system:
"honest," "robust," "non-sycophantic." The group-theoretic results model both
of the ways this inference can be wrong. The threshold phenomenon says that
"passes 62% of probes" may certify nothing while "passes 64%" certifies
everything — and that the certifying threshold differs per property, is
discovered by hard mathematics rather than convention, and is witnessed at its
boundary by adversarial structure, not random failure. The non-recognizability
phenomenon is sharper still: for some properties, *no* rate of local passing
certifies the global claim, because the property lives in the interaction of
scales that local sampling cannot see. The sycophancy literature has already
met this empirically: training a model out of one form of sycophancy does not
remove the others [9, citing Ye et al.], and multi-turn coalition effects
appear only in configurations no single-turn probe contains — models that
resist a majority but capitulate to a unanimous bloc, or strengthen their
position when a single ally dissents [9]. The mathematical vocabulary offers
the right question to ask of every benchmark: *is the property you are
claiming even recognizable from the samples you are drawing, and if so, do you
know the threshold, and do you know the extremal counterexample that sits just
below it?* An eval that cannot answer is measuring, not certifying.

---

## 5. Retraction, or lossless forgetting

A cycle set — Rump's algebraic packaging of set-theoretic solutions to the
Yang–Baxter equation — is called **retractable** when two distinct elements
\(x, x'\) act identically on everything: \(x * y = x' * y\) for all \(y\) [4].
Retraction is then a quotient: collapse the indistinguishable elements,
obtain a smaller object, and (in principle) reconstruct the original as an
extension. The new result is that every nontrivial finite *quasilinear* cycle
set is retractable — under the right structural hypotheses, a lossless
compression step is always available [4].

**Analogy.** This is the cleanest mathematical statement I know of the problem
that context engineering has been solving by trial and error. The ACE
framework [10] documents two failure modes of naive context management:
**brevity bias**, where optimizers compress away the domain-specific tactics
that actually carry performance, and **context collapse**, where iterative
monolithic rewriting erodes a context into a short, uninformative summary. Its
remedy is to treat contexts as evolving playbooks with structured incremental
updates — grow-and-refine rather than rewrite-and-shrink.

The cycle-set lens says why the distinction is principled and not a matter of
taste. A *retraction* identifies entries that are **behaviorally
indistinguishable** — same action on every downstream continuation — and is
therefore safe by construction; the compressed object supports reconstruction.
A *summary* identifies entries that are merely similar on the surface, and
every such identification is a bet that the discarded difference never
mattered. Context collapse is what it looks like when those bets compound.
The engineering translation (speculative, but directly testable): define
equivalence of context entries extensionally, by identity of induced behavior
across a battery of downstream tasks, and license deletion *only* along that
equivalence. Rump's theorem even suggests an optimistic research question —
whether realistic agent-memory structures always admit a nontrivial retraction,
i.e., whether some lossless forgetting is always available. That is a
conjecture about artifacts we can actually build, inspired by a theorem about
artifacts we cannot.

---

## 6. Noise budgets and composed stabilizers

The stochastic-robustness paper on the shelf [5] proves three graded results
about SDEs. Stability survives perturbations bounded by a *state-dependent*
function that vanishes at the equilibrium and is positive elsewhere.
Input-to-state stability (ISS) — bounded disturbance in, bounded deviation
out — holds when the input is scaled by a state-dependent factor. And in the
strongest regime, exponential stability, proportional perturbations are
tolerated with *no* scaling at all. On top of this, the authors build
**backstepping**: a compositional method that stabilizes an inner subsystem
first and then extends the certificate outward through the interconnection.

**Analogy.** PALS's LAW — the architectural rule this repository builds
under — states that LLMs *will* produce errors as a statistical property of
the model class, so the absence of a verification layer is a design defect
rather than a runtime bug. That is, precisely, a decision to model the agent
as a stochastic system and to engineer for disturbance rather than hope for
its absence. The SDE results then read as a taxonomy of what verification
buys. A weak verifier corresponds to the first regime: the pipeline tolerates
error only where the tolerable-error bound is generous, and the bound
*vanishes near the target* — which matches the practitioner's experience that
the final integration steps, closest to "done," are exactly where small
unverified edits are least survivable. A strong verifier — a kernel-checked
proof, a deterministic test oracle — moves the pipeline into the exponential
regime, where errors proportional to the work done are absorbed without
special handling. And backstepping is the honest name for multi-stage agent
design: you do not stabilize a five-stage pipeline in one gesture; you
stabilize the innermost loop (generate-verify at the single-edit level), prove
that certificate, and only then compose outward, stage by stage, letting each
layer's Lyapunov argument become an assumption of the next. Cascades built in
the other direction — orchestration first, verification "later" — are
attempting the composition without the inner certificate, and the SDE
literature's verdict on that is not encouraging.

---

## 7. Two spaces, same shape

Brady, Delucchi, and Watt study two classifying spaces for the same Artin
group: the Salvetti complex, built from the permutahedron, and the
non-crossing-partition complex, built from an entirely different combinatorial
lattice [6]. Homotopy theory guarantees *abstractly* that the two are
equivalent. The paper's contribution is the part abstraction does not give:
an **explicit** homotopy equivalence, constructed by triangulating the
permutahedron — a common refinement into which both descriptions decompose,
with the key incidence structure characterized combinatorially (via Bruhat
intervals whose quotient is a Coxeter element).

**Analogy.** Agent architectures proliferate: single-agent-with-tools,
planner–executor, judge panels, pipeline-of-specialists. Practitioners
routinely assert that two such designs are "equivalent" — same capability,
different plumbing — and the assertion is usually true in the useless,
abstract sense: some behavioral equivalence exists. What migration between
architectures actually requires is the Salvetti-to-non-crossing move: an
explicit common refinement — a decomposition of both systems into shared
atomic obligations (this check, this transformation, this piece of evidence)
fine enough that each architecture is visibly an assembly of the same pieces.
Absent the explicit map, an architecture migration is a rewrite wearing an
equivalence as a costume, and every eval must be re-earned from zero. With
it, test artifacts, invariants (Section 2), and noise budgets (Section 6)
transport along the refinement. The mathematics is blunt about where the work
lives: existence of the equivalence was never the problem; *triangulating* is
the problem.

---

## 8. The shelves are already merging

The strongest reason to take these analogies seriously is that the corpus
already contains their fusion, twice.

The Aria study [11] hands entire lemma sets of Iris — the state-of-the-art
concurrent separation logic — to a general code agent wrapped in a harness
that enforces soundness (the kernel must close every proof), completeness (no
obligation silently dropped), and termination. Where prior systems wired a
fixed, human-designed proof strategy into the model and proved 12–48% of
their targets, the free agent under a strict harness proves *all* of them:
4,257 core lemmas plus 217 verifying Rust standard-library primitives, with
no expert intervention [11]. The design lesson is the essay's thesis in
miniature: constrain the *certificate*, not the *strategy*. Every result in
Sections 2–7 points the same way — invariants, obstructions, thresholds,
retractions, Lyapunov functions are all certificate languages, all silent
about how the search is conducted.

The Lean 4 metagame study [12] runs the same architecture in the opposite
direction: an untrusted discovery layer proposes strategic facts about a
competitive card game, and a trusted verifier machine-checks them — a Nash
equilibrium certified by simultaneous best-response checks across all
fourteen strategies, a "popularity paradox" (the most-played deck is
provably suboptimal against the field) established over exact data
representations, roughly 200 of its 2,627 theorems directly verifying
empirical claims [12]. The authors name the genre: *proof-carrying
analytics*. An agent's claim about the world ships with a certificate a
kernel can check, and re-checking against new data requires no re-auditing
of the reasoning.

Untrusted search, trusted certification, explicit invariants, sharp
thresholds, principled compression, composed stability, explicit
equivalences. None of this is exotic. All of it already exists — on the
other shelf.

---

## 9. An agenda in six obligations

The essay compresses to six imperatives, each inherited from a theorem:

1. **Every "cannot" needs an invariant.** A safety claim without an
   exhibited, dynamics-closed separating set is a mood, not an argument [2].
2. **Compute the obstruction before attempting the split.** Ask whether the
   task's target exists independently of the collaboration; where it does
   not, stop designing handoffs [1], [8].
3. **Know your threshold — and your extremal counterexample.** An eval
   certifies only above a sharp, property-specific bound, if the property is
   recognizable from local samples at all [3], [9].
4. **Delete only along behavioral equivalence.** Compression that identifies
   indistinguishable context is a retraction; everything else is a compounding
   bet [4], [10].
5. **Stabilize inside-out.** Verify the innermost loop, then compose
   outward; a strong inner certificate buys proportional error tolerance for
   the whole cascade [5].
6. **Equivalence claims owe a triangulation.** Two architectures are
   interchangeable only when both decompose into the same explicit atoms
   [6].

None of these obligations is discharged by better models. All of them are
discharged by better mathematics around the models — which is to say, the
first shelf was never a different subject. It was the manual.

---

## References

Corpus provenance: all works below were consulted in a private document
corpus; identifiers are reproduced exactly as they appear in the source texts.

1. T. Ariel, R. Rubio, M. Zambon, *On the Dirac complement problem*,
   arXiv:2607.15428 [math.SG], 2026.
2. C. Bizière, J. Leroux, G. Sutre, *Solving the Reachability Problem for
   Branching Vector Addition Systems via Semilinear Inductive Invariants*,
   arXiv:2607.09558 [cs.LO], 2026.
3. A. Lucchini, *Supersoluble groups and the probability of generating a
   supersoluble subgroup*, arXiv:2607.08165 [math.GR], 2026.
4. *Proof of Rump's Retraction Conjecture for Quasilinear Cycle Sets*,
   arXiv:2607.08609 [math.QA], 2026.
5. R. Moldenhauer, D. Nešić, M. Granzotto, R. Postoyan, A. Teel, *On
   robustness, input-to-state stability and backstepping for stochastic
   differential equations*, arXiv:2607.09127 [eess.SY], 2026.
6. T. Brady, E. Delucchi, C. Watt, *Triangulating the Permutahedron*,
   arXiv:2607.07567 [math.GR], 2026.
7. *The Human-AI Substitution Principle: When will you be replaced by AI in
   your organization?*, arXiv:2607.20781 [cs.AI], 2026.
8. *The Boundaries of Automation: A Theory of Persistent Human
   Participation*, arXiv:2607.21547 [cs.AI], 2026.
9. *Beyond Sycophancy: Structured Resistance and Compliance in LLM Moral
   Reasoning*, arXiv:2607.21558 [cs.AI], 2026.
10. *Agentic Context Engineering: Evolving Contexts for Self-Improving
    Language Models* (ACE), arXiv:2510.04618 [cs.LG], ICLR 2026.
11. *Harnessing Code Agents for Automatic Software Verification* (Aria),
    arXiv:2607.06341 [cs.FL], 2026.
12. A. F. Ramos, T. Soria, *From Rules to Nash Equilibria: A Lean 4 Case
    Study in Game-Theoretic Analysis of a Competitive Trading Card Game*,
    2026. (arXiv identifier not present in the excerpted text; cited from the
    corpus copy.)
