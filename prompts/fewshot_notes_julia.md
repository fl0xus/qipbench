How the description above maps to the model:

- The two overtime reservations per team are the decisions taken before anything is known.
  They are existential and belong to block 1.
- "exactly one of the two customers places a rush order" is not our decision. Both order
  variables get quantifier "all", and the constraint forcing exactly one of them carries
  `YasolConstraint, quantifier = "all"`.
- That "exactly one" would read `r1 + r2 == 1`, but equalities cannot be used here: it is
  split into `<= 1` and `>= 1`, two constraints with their own names and the same
  quantifier. The pair says the same thing.
- The freelancer is hired after the order is known, so it sits in a later block than the
  order variables. Block numbers, not the order of the lines in the file, are what encode
  who knows what.
- "only one freelancer available" is a constraint on our own decisions and therefore
  `quantifier = "exists"`, like the two requirement constraints.
- Constraints are written with all variables on the left and a constant on the right, and
  the requirement increase caused by a rush order appears as a term with the order
  variable, not as a change to the right-hand side.
