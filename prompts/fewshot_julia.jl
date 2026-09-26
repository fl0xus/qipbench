using JuMP, YasolSolver

model = Model(() -> YasolSolver.Optimizer())
set_optimizer_attribute(model, "solver path", ENV["YASOL_BIN"])
set_optimizer_attribute(model, "output info", 1)
set_optimizer_attribute(model, "problem file name", "model.qlp")

@variable(model, m1, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 1)
@variable(model, m2, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 1)
@variable(model, v1, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 1)
@variable(model, v2, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 1)

@variable(model, r1, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "all", block = 2)
@variable(model, r2, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "all", block = 2)

@variable(model, f1, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 3)
@variable(model, f2, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 3)

@constraint(model, c1, -3 * m1 - 3 * m2 - 3 * f1 + 3 * r1 <= -3, YasolConstraint, quantifier = "exists")
@constraint(model, c2, -3 * v1 - 3 * v2 - 3 * f2 + 3 * r2 <= -4, YasolConstraint, quantifier = "exists")
@constraint(model, c3, 1 * f1 + 1 * f2 <= 1, YasolConstraint, quantifier = "exists")
@constraint(model, c4a, 1 * r1 + 1 * r2 <= 1, YasolConstraint, quantifier = "all")
@constraint(model, c4b, 1 * r1 + 1 * r2 >= 1, YasolConstraint, quantifier = "all")

@objective(model, Min, 4 * m1 + 4 * m2 + 4 * v1 + 4 * v2 + 2 * f1 + 2 * f2)

optimize!(model)
