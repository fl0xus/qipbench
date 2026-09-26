How the description above maps to the model:

- The two overtime reservations per team are the decisions taken before anything is known.
  They form the first block and are existential: `m1 m2 v1 v2` under EXISTS, listed first
  under ORDER.
- "exactly one of the two customers places a rush order" is not a decision of ours. Both
  `r1` and `r2` go under ALL, and `U_one_order` makes the choice exactly one — a universal
  constraint, marked by the `U_` prefix.
- The freelancer is hired after the order is known, so `f1 f2` come after `r1 r2` under
  ORDER even though they are existential again. The order of the blocks is what encodes
  who knows what.
- "only one freelancer available" is an ordinary constraint on our own decisions and gets
  the `E_` prefix, like the two requirement constraints.
- A rush order raising a requirement by 3 appears as `- 3 r1` on the left-hand side, not as
  a term on the right: only constants stand right of the comparison.
