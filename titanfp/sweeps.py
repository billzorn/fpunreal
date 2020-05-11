import math
import operator

from .fpbench import fpcparser
from .arithmetic import mpmf, ieee754, evalctx, analysis
from .arithmetic.mpmf import Interpreter

from .sweep import search


sqrt_core = '''(FPCore sqrt_bfloat_limit (a residual_bound)
 :precision {overall_prec}

 (while* (and (! :titanic-analysis skip (< steps (# 10))) (>= (fabs residual) residual_bound))
  ([x a (! :precision {diff_prec} (- x
                                     (! :precision {scale_prec} (/ residual (* 2 x)))))]
   [residual (! :precision {res_prec} (- (* x x) a))
             (! :precision {res_prec} (- (* x x) a))]
   [steps (! :titanic-analysis skip (# 0))
          (! :titanic-analysis skip (# (+ 1 steps)))])
  (cast x))

)
'''

sqrt_bab_core = '''(FPCore bab_bfloat_limit (a residual_bound)
 :precision {overall_prec}

 (while* (and (! :titanic-analysis skip (< steps (# 10))) (>= (fabs residual) residual_bound))
  ([x a (! :precision {diff_prec} (* 1/2 (+ x
                                            (! :precision {scale_prec} (/ a x)))))]
   [residual (! :precision {res_prec} (- (* x x) a))
             (! :precision {res_prec} (- (* x x) a))]
   [steps (! :titanic-analysis skip (# 0))
          (! :titanic-analysis skip (# (+ 1 steps)))])
  (cast x)
 )

)
'''


def linear_ulps(x, y):
    smaller_n = min(x.n, y.n)
    x_offset = x.n - smaller_n
    y_offset = y.n - smaller_n

    x_c = x.c << x_offset
    y_c = y.c << y_offset

    return x_c - y_c


formatted = sqrt_core.format(
    overall_prec = '(float 8 16)',
    res_prec = '(float 5 16)',
    diff_prec = '(float 5 16)',
    scale_prec = '(float 5 16)',
)

evaltor = Interpreter()
core = fpcparser.compile1(formatted)

bf16 = ieee754.ieee_ctx(8,16)
f64 = ieee754.ieee_ctx(11,64)
one = mpmf.MPMF(1, ctx=bf16)
ten = mpmf.MPMF(10, ctx=bf16)
current = one
one_to_ten = []
reference_results = []
while current <= ten:
    one_to_ten.append(current)
    reference = float(str(current))
    reference_results.append((current.sqrt(ctx=bf16), math.sqrt(reference)))
    current = current.next_float()

print(f'{len(one_to_ten)!s} bfloat16 test cases between 1 and 10 inclusive')

# def run_tests(core, bound):

#     results = []

#     for arg, ref in zip(one_to_ten, reference_results):
#         evaltor = Interpreter()
#         als, bc_als = analysis.DefaultAnalysis(), analysis.BitcostAnalysis()
#         #evaltor.analyses = [als, bc_als]
#         result = evaltor.interpret(core, (arg, bound))
#         err = (ref.sub(result)).fabs()
#         steps = evaltor.evals

#         results.append((str(arg), str(result), str(err), steps))

#     return results

# def oob(results):
#     counted = 0
#     maxerr = 0
#     sumerr = 0
#     sumcount = 0
#     for arg, result, err, steps in results:
#         if steps > 200:
#             counted += 1
#         abserr = abs(float(err))
#         if math.isfinite(abserr):
#             sumerr += abserr
#             sumcount += 1
#         # if abserr > 0.01:
#         #     print(arg, result, err, steps)
#         if abserr > maxerr:
#             maxerr = abserr
#     # print(f'{counted!s} inputs ran for more than 200 steps.')
#     # print(f'worst point was {maxerr!s}.')
#     return counted, maxerr, sumerr / sumcount


# new, with bitcost
def run_and_eval_bitcost(core, bound):

    timeouts = 0
    infs = 0
    worst_abserr = 0
    total_abserr = 0
    worst_ulps = 0
    total_ulps = 0
    worst_bitcost = 0
    total_bitcost = 0

    for arg, (ref_lo, ref_hi) in zip(one_to_ten, reference_results):
        evaltor = Interpreter()
        als = analysis.BitcostAnalysis()
        evaltor.analyses = [als]
        result = evaltor.interpret(core, (arg, bound))

        err = ref_hi - float(str(result))
        ulps = linear_ulps(result, ref_lo)
        steps = evaltor.evals
        bitcost = als.bits_requested

        abserr = abs(err)
        absulps = abs(ulps)

        if steps > 200:
            timeouts += 1

        if math.isfinite(abserr):
            if abserr > worst_abserr:
                worst_abserr = abserr
            total_abserr += abserr
            if absulps > worst_ulps:
                worst_ulps = absulps
            total_ulps += absulps
        else:
            worst_abserr = math.inf
            total_abserr = math.inf
            worst_ulps = math.inf
            total_ulps = math.inf
            infs += 1

        if bitcost > worst_bitcost:
            worst_bitcost = bitcost
        total_bitcost += bitcost

    return timeouts, infs, worst_abserr, total_abserr, worst_ulps, total_ulps, worst_bitcost, total_bitcost



def sweep_stage(bound, expbits, res_bits, diff_bits, scale_bits):

    res_nbits = expbits + res_bits
    diff_nbits = expbits + diff_bits
    scale_nbits = expbits + scale_bits

    formatted = sqrt_core.format(
        overall_prec = '(float 8 16)',
        res_prec = f'(float {expbits!s} {res_nbits!s})',
        diff_prec = f'(float {expbits!s} {diff_nbits!s})',
        scale_prec = f'(float {expbits!s} {scale_nbits!s})',
    )
    core = fpcparser.compile1(formatted)

    return run_and_eval_bitcost(core, bound)

    # results = run_tests(core, bound)
    # #counted, worst, avg = oob(results)
    # return oob(results)



def bab_stage(bound, expbits, res_bits, diff_bits, scale_bits):

    res_nbits = expbits + res_bits
    diff_nbits = expbits + diff_bits
    scale_nbits = expbits + scale_bits

    formatted = sqrt_bab_core.format(
        overall_prec = '(float 8 16)',
        res_prec = f'(float {expbits!s} {res_nbits!s})',
        diff_prec = f'(float {expbits!s} {diff_nbits!s})',
        scale_prec = f'(float {expbits!s} {scale_nbits!s})',
    )
    core = fpcparser.compile1(formatted)

    return run_and_eval_bitcost(core, bound)


# New, to facilitate using search interface
import random

def init_bound():
    return 1/100

def neighbor_bound(x):
    yield 1/100

def init_expbits():
    return random.randint(3,8)

def neighbor_expbits(x):
    nearby = 1
    for neighbor in range(x-nearby, x+nearby+1):
        if 1 <= neighbor <= 8:
            yield neighbor

def init_p():
    return random.randint(1, 16)

def neighbor_p(x):
    nearby = 3
    for neighbor in range(x-nearby, x+nearby+1):
        if 1 <= neighbor <= 32:
            yield neighbor


newton_inits = [
    init_bound,
    init_expbits,
    init_p,
    init_p,
    init_p
]

newton_neighbors = [
    neighbor_bound,
    neighbor_expbits,
    neighbor_p,
    neighbor_p,
    neighbor_p
]

newton_metrics = (operator.lt,) * 5

def run_random():
    return search.sweep_random_init(sweep_stage, newton_inits, newton_neighbors, newton_metrics)


from multiprocessing import Pool


def sweep(stage_fn):
    bound = 1/100

    minexp = 3
    maxexp = 5
    minp = 2
    maxp = 20

    # minexp = 3
    # maxexp = 4
    # minp = 12
    # maxp = 14

    cfgs = 0

    frontier = []
    metrics = (operator.lt,) * 8

    with Pool() as p:
        result_buf = []

        print('building async result list')

        for expbits in range(minexp,maxexp+1):
            for res_bits in range(minp, maxp+1):
                for diff_bits in range(minp, maxp+1):
                    for scale_bits in range(minp, maxp+1):
                        args = bound, expbits, res_bits, diff_bits, scale_bits
                        result_buf.append((args, p.apply_async(stage_fn, args)))

        print(f'waiting for {len(result_buf)!s} results to come back')

        for args, result in result_buf:
            config = args
            bound, expbits, res_bits, diff_bits, scale_bits = config
            result_get = result.get()
            timeouts, infs, worst_abserr, total_abserr, worst_ulps, total_ulps, worst_bitcost, total_bitcost = result_get

            cfgs += 1
            print(f' -- {cfgs!s} -- ran {config!r}, got {result_get!r}')

            if True: # timeouts == 0 and infs == 0:
                frontier_elt = (config, result_get)

                updated, frontier = search.update_frontier(frontier, frontier_elt, metrics)
                if updated:
                    print('New frontier:')
                    search.print_frontier(frontier)

    print(f'tried {cfgs!s} configs, done\n')
    print('Final frontier:')
    search.print_frontier(frontier)
    print(flush=True)

    return frontier


def go():

    frontier_newton = sweep(sweep_stage)
    frontier_babylonian = sweep(bab_stage)

    print('\n\n\n\n')
    print('final frontier for newton:')
    search.print_frontier(frontier_newton)
    print('final frontier for babylonian:')
    search.print_frontier(frontier_babylonian)

    return frontier_newton, frontier_babylonian





# there are 5 metrics:

# how many examples timed out
# how many examples reported inf
# the worst error (not residual!) of an example
# the worst individual bitcost
# the total bitcost (sum of examples)

# And here are the frontiers:

# tried 11375 configs, done

# Final frontier:
# {
#   [4, 14, 15, 8] : (0, 0, 0.010319312515875811, 2722, 583906)
#   [4, 15, 15, 8] : (0, 0, 0.009583499933366824, 2309, 586235)
#   [4, 15, 15, 9] : (0, 0, 0.009225947422650371, 2324, 589008)
#   [4, 15, 15, 10] : (0, 0, 0.009225947422650371, 1900, 592208)
#   [4, 15, 16, 9] : (0, 0, 0.008814576415149933, 2342, 592590)
#   [4, 15, 16, 10] : (0, 0, 0.008814576415149933, 1914, 596226)
#   [4, 15, 16, 11] : (0, 0, 0.008540409355627165, 1926, 599862)
#   [4, 16, 15, 10] : (0, 0, 0.008913438213184577, 1914, 595794)
# }
# Final bounded frontier:
# {
#   [4, 15, 15, 8] : (0, 0, 0.009583499933366824, 2309, 586235)
#   [4, 15, 15, 9] : (0, 0, 0.009225947422650371, 2324, 589008)
#   [4, 15, 15, 10] : (0, 0, 0.009225947422650371, 1900, 592208)
#   [4, 15, 16, 9] : (0, 0, 0.008814576415149933, 2342, 592590)
#   [4, 15, 16, 10] : (0, 0, 0.008814576415149933, 1914, 596226)
#   [4, 15, 16, 11] : (0, 0, 0.008540409355627165, 1926, 599862)
#   [4, 16, 15, 10] : (0, 0, 0.008913438213184577, 1914, 595794)
# }

# ([([4, 14, 15, 8], (0, 0, 0.010319312515875811, 2722, 583906)), ([4, 15, 15, 8], (0, 0, 0.009583499933366824, 2309, 586235)), ([4, 15, 15, 9], (0, 0, 0.009225947422650371, 2324, 589008)), ([4, 15, 15, 10], (0, 0, 0.009225947422650371, 1900, 592208)), ([4, 15, 16, 9], (0, 0, 0.008814576415149933, 2342, 592590)), ([4, 15, 16, 10], (0, 0, 0.008814576415149933, 1914, 596226)), ([4, 15, 16, 11], (0, 0, 0.008540409355627165, 1926, 599862)), ([4, 16, 15, 10], (0, 0, 0.008913438213184577, 1914, 595794))], [([4, 15, 15, 8], (0, 0, 0.009583499933366824, 2309, 586235)), ([4, 15, 15, 9], (0, 0, 0.009225947422650371, 2324, 589008)), ([4, 15, 15, 10], (0, 0, 0.009225947422650371, 1900, 592208)), ([4, 15, 16, 9], (0, 0, 0.008814576415149933, 2342, 592590)), ([4, 15, 16, 10], (0, 0, 0.008814576415149933, 1914, 596226)), ([4, 15, 16, 11], (0, 0, 0.008540409355627165, 1926, 599862)), ([4, 16, 15, 10], (0, 0, 0.008913438213184577, 1914, 595794))])




# # larger search space, for newton and babylonian

# final frontier for newton:
# [
#     ([4, 10, 11, 4], (0, 0, 0.010319312515875811, 2722, 583906)),
#     ([4, 11, 11, 4], (0, 0, 0.009583499933366824, 2309, 586235)),
#     ([4, 11, 11, 5], (0, 0, 0.009225947422650371, 2324, 589008)),
#     ([4, 11, 11, 6], (0, 0, 0.009225947422650371, 1900, 592208)),
#     ([4, 11, 12, 5], (0, 0, 0.008814576415149933, 2342, 592590)),
#     ([4, 11, 12, 6], (0, 0, 0.008814576415149933, 1914, 596226)),
#     ([4, 11, 12, 7], (0, 0, 0.008540409355627165, 1926, 599862)),
#     ([4, 12, 11, 6], (0, 0, 0.008913438213184577, 1914, 595794)),
# ]
# final frontier for babylonian:
# [
#     ([3, 8, 10, 11], (0, 0, 0.00504269614631192, 1876, 579698)),
#     ([3, 9, 11, 11], (0, 0, 0.004352769716506222, 2350, 596824)),
#     ([3, 9, 11, 12], (0, 0, 0.004352769716506222, 1912, 598042)),
#     ([3, 10, 11, 10], (0, 0, 0.004691951575984676, 2357, 596218)),
# ]
# ([([4, 10, 11, 4], (0, 0, 0.010319312515875811, 2722, 583906)), ([4, 11, 11, 4], (0, 0, 0.009583499933366824, 2309, 586235)), ([4, 11, 11, 5], (0, 0, 0.009225947422650371, 2324, 589008)), ([4, 11, 11, 6], (0, 0, 0.009225947422650371, 1900, 592208)), ([4, 11, 12, 5], (0, 0, 0.008814576415149933, 2342, 592590)), ([4, 11, 12, 6], (0, 0, 0.008814576415149933, 1914, 596226)), ([4, 11, 12, 7], (0, 0, 0.008540409355627165, 1926, 599862)), ([4, 12, 11, 6], (0, 0, 0.008913438213184577, 1914, 595794))], [([3, 8, 10, 11], (0, 0, 0.00504269614631192, 1876, 579698)), ([3, 9, 11, 11], (0, 0, 0.004352769716506222, 2350, 596824)), ([3, 9, 11, 12], (0,
# 0, 0.004352769716506222, 1912, 598042)), ([3, 10, 11, 10], (0, 0, 0.004691951575984676, 2357, 596218))])