# Installs YasolSolver.jl into a shared project environment and precompiles it, so
# requests do not pay the compilation cost.
#
# YasolSolver.jl's Project.toml pins JuMP to 1.0.0 and MathOptInterface to <= 1.1.1.
# The resolved environment is therefore deliberately old; that is upstream's bound,
# not a choice made here.

using Pkg

Pkg.activate(ENV["JULIA_PROJECT"])
# Pinned for the same reason the solver is: benchmark results must name an exact revision.
Pkg.add(url = "https://github.com/MichaelHartisch/YasolSolver.jl", rev = "0e989fb74827ed7749f73ff903e78ef227822f14")
# JuMP is a dependency of YasolSolver, but user code says `using JuMP`, which only
# resolves if JuMP is a direct dependency of the active project.
Pkg.add("JuMP")
Pkg.precompile()

using YasolSolver
using JuMP

@info "Julia environment ready" YasolSolver = pkgversion(YasolSolver) JuMP = pkgversion(JuMP)
