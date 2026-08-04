# Bilevel problem from Moore & Bard, as used in the YasolSolver.jl README.
# Send this to POST /solve/julia.

using JuMP, YasolSolver

model = Model(() -> YasolSolver.Optimizer())

# The container exports YASOL_BIN; the working directory is a fresh scratch dir
# that already contains Yasol.ini.
set_optimizer_attribute(model, "solver path", ENV["YASOL_BIN"])
set_optimizer_attribute(model, "time limit", 30)
set_optimizer_attribute(model, "output info", 1)
set_optimizer_attribute(model, "problem file name", "example.qlp")

@variable(model, x1, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 1)
@variable(model, x2, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 2)
@variable(model, x3, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "all", block = 3)
@variable(model, x4, binary = true, lower_bound = 0, upper_bound = 1, YasolVariable, quantifier = "exists", block = 4)

@constraint(model, con1, 1 * x1 - 2 * x2 + 1 * x3 - 1 * x4 <= 1, YasolConstraint, quantifier = "exists")
@constraint(model, con2, 1 * x1 + 1 * x2 + 1 * x3 - 1 * x4 <= 2, YasolConstraint, quantifier = "exists")
@constraint(model, con3, 1 * x1 + 1 * x2 + 1 * x3 <= 2, YasolConstraint, quantifier = "all")

@objective(model, Min, -1 * x1 - 2 * x2 + 2 * x3 + 1 * x4)

optimize!(model)

@show termination_status(model)
@show solve_time(model)
