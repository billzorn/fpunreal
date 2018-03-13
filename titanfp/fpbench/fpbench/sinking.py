"""Sinking point -
A discrete approximation of real numbers with explicit significance tracking.
Implemented really badly in one file.
"""


import typing
import sys
import random
import re

def bitmask(n):
    if n > 0:
        return (1 << n) - 1
    else:
        return (-1) << (-n)


# Binary conversions are relatively simple for numpy's floating point types.
# float16 : w = 5,  p = 11
# float32 : w = 8,  p = 24
# float64 : w = 11, p = 53
# float128: unsupported, not an IEEE 754 128-bit float, possibly 80bit x87?
#           doc says this uses longdouble on the underlying system
import numpy as np


def np_byteorder(ftype):
    bo = np.dtype(ftype).byteorder
    if bo == '=':
        return sys.byteorder
    elif bo == '<':
        return 'little'
    elif bo == '>':
        return 'big'
    else:
        raise ValueError('unknown numpy byteorder {} for dtype {}'.format(repr(bo), repr(ftype)))


def xfloat(f):
    if isinstance(f, np.float16):
        w = 5
        pbits = 10
    elif isinstance(f, np.float32):
        w = 8
        pbits = 23
    elif isinstance(f, np.float64):
        w = 11
        pbits = 52
    else:
        raise TypeError('expected np.float{{16,32,64}}, got {}'.format(repr(type(f))))

    emax = (1 << (w - 1)) - 1

    bits = int.from_bytes(f.tobytes(), np_byteorder(type(f)))

    S = bits >> (w + pbits) & bitmask(1)
    E = bits >> (pbits) & bitmask(w)
    C = bits & bitmask(pbits)

    e = E - emax

    if E == 0:
        # subnormal
        return S != 0, -emax - (pbits - C.bit_length()), C
    elif e <= emax:
        # normal
        return S != 0, e, C | (1 << pbits)
    else:
        # nonreal
        raise ValueError('nonfinite value {}'.format(repr(f)))


def mkfloat(s, e, c, ftype=np.float64):
    if ftype == np.float16:
        w = 5
        p = 11
        pbits = 10
        nbytes = 2
    elif ftype == np.float32:
        w = 8
        p = 24
        pbits = 23
        nbytes = 4
    elif ftype == np.float64:
        w = 11
        p = 53
        pbits = 52
        nbytes = 8
    else:
        raise TypeError('expected np.float{{16,32,64}}, got {}'.format(repr(type(f))))

    emax = (1 << (w - 1)) - 1
    emin = 1 - emax

    cbits = c.bit_length()

    if e < emin:
        # subnormal
        lz = (emin - 1) - e
        if lz > pbits or (lz == pbits and cbits > 0):
            raise ValueError('exponent out of range: {}'.format(e))
        elif lz + cbits > pbits:
            raise ValueError('too much precision: given {}, can represent {}'.format(cbits, pbits - lz))
        S = 1 if s else 0
        E = 0
        C = c << (lz - (pbits - cbits))
    elif e <= emax:
        # normal
        if cbits > p:
            raise ValueError('too much precision: given {}, can represent {}'.format(cbits, p))
        elif cbits < p:
            print('Warning: inventing {} low order bits!'.format(p - cbits))
        S = 1 if s else 0
        E = e + emax
        C = (c << (p - cbits)) & bitmask(pbits)
    else:
        # overflow
        raise ValueError('exponent out of range: {}'.format(e))

    return np.frombuffer(
        ((S << (w + pbits)) | (E << pbits) | C).to_bytes(nbytes, np_byteorder(ftype)),
        dtype=ftype, count=1, offset=0,
    )[0]


# debugging

def _nprt(x):
    print(repr(x))
    return mkfloat(*xfloat(x), type(x))

def _check_conv(i, ftype):
    if ftype == np.float16:
        nbytes = 2
    elif ftype == np.float32:
        nbytes = 4
    elif ftype == np.float64:
        nbytes = 8
    else:
        raise TypeError('expected np.float{{16,32,64}}, got {}'.format(repr(type(f))))

    try:
        f = np.frombuffer(i.to_bytes(nbytes, np_byteorder(ftype)), dtype=ftype, count=1, offset=0)[0]
        s, e, c = xfloat(f)
        f2 = mkfloat(s, e, c, ftype)
        if f != f2:
            print(repr(f), repr(f2), s, e, c)
    except ValueError as e:
        if not (np.isinf(f) or np.isnan(f)):
            print(repr(f), repr(f2), s, e, c)
            print('  ' + repr(e))

def _test_np():
    print('watch for output...')
    for i in range(1 << 16):
        _check_conv(i, np.float16)
    for i in range(1 << 16):
        _check_conv(random.randint(0, 1 << 32), np.float32)
    for i in range(1 << 16):
        _check_conv(random.randint(0, 1 << 64), np.float64)
    print('...done')


# gmpy2 helpers
import gmpy2 as gmp
mpfr = gmp.mpfr
mpz = gmp.mpz
mpfr_t = type(gmp.mpfr())

def exactctx(prec, emin, emax):
    return gmp.context(
        precision=max(2, prec),
        emin=min(-1, emin),
        emax=max(+1, emax),
        trap_underflow=True,
        trap_overflow=True,
        trap_inexact=True,
        trap_invalid=True,
        trap_erange=True,
        trap_divzero=True,
        trap_expbound=True,
    )


def sigbits(m):
    z = mpz(m)
    trailing_zeros = z.bit_scan1(0)
    if trailing_zeros is None:
        return 0
    else:
        return z.bit_length() - trailing_zeros


def to_shortest_mantissa_exp(r):
    """This destroys info about the precision"""
    m, e = r.as_mantissa_exp()
    trailing_zeros = m.bit_scan1(0)
    if trailing_zeros is None:
        return 0, None
    else:
        return m >> trailing_zeros, e + trailing_zeros


def to_mantissa_exp(r):
    m, e = r.as_mantissa_exp()
    if m == 0:
        return 0, None
    else:
        return m, e


def from_mantissa_exp(m, e):
    if m == 0:
        return mpfr(0, 2)
    mbits = m.bit_length()
    ebits = e.bit_length()
    esig = sigbits(e)
    exp = int(e)
    with exactctx(mbits, min(ebits, mbits, exp), max(ebits, mbits, exp + mbits)):
        rexp = scale = rm = result = None
        try:
            rexp = mpfr(e, max(2, esig))
            scale = mpfr(gmp.exp2(rexp), 2)
            rm = mpfr(m)
            result = gmp.mul(rm, scale)
        except Exception as exc:
            print(exc)
            print(m, e)
            print(mbits, ebits, exp, esig)
            print(result, rm, scale, rexp)
            print(gmp.get_context())
        return result


def withprec(p, op, *args):
    with gmp.context(precision=max(2, p), trap_expbound=True) as gmpctx:
        result = op(*args)
        return result, gmpctx


# more debugging

def _gmprt(x1, x2=None):
    if x2 is None:
        x = mpfr(x1, 53)
    else:
        x = from_mantissa_exp(x1, x2)
    result = from_mantissa_exp(*to_mantissa_exp(x))
    print(repr(x))
    return(x)

def _test_gmp():
    print('watch for output...')
    for i in range(1 << 16):
        m = random.randint(0, 1 << 256) << (random.randint(0, 256) if random.randint(0, 2) else 0)
        e = random.randint(-126, 127) if random.randint(0, 2) else random.randint(-(1 << 32), (1 << 32))
        x1 = from_mantissa_exp(m, e)
        x2 = from_mantissa_exp(*to_mantissa_exp(x1))
        if x1 != x2:
            print(m, e, x1, x2)
        if x1.precision != x2.precision:
            print(x1.precision, x2.precision, x1, x2)
    print('...done')


_DEFAULT_PREC = 53


def enclose(lower, upper):
    """Return the sink with the smallest interval that encloses lower and upper.
    Upper and lower must be exact sinks, with upper <= lower.
    TODO: auto bounds?
    TODO: other kinds of intervals?
    """
    if lower._inexact or upper._inexact:
        raise ValueError('enclose: must have exact arguments, got [{} and {}]'.format(lower, upper))
    elif lower == upper:
        return Sink(lower) if lower._n < upper._n else Sink(upper)
    elif not lower < upper:
        raise ValueError('enclose: arguments out of order, not {} < {}'.format(lower, upper))

    zero = Sink(0)
    # because upper != lower, the distance between them must be larger than the interval size
    # with this n
    min_n = min(lower._n, upper._n) - 1
    
    if lower < zero and upper > zero:
        # binsearch around zero
        offset = 1
        n_lo = n_hi = min_n
        bound_lo, bound_hi = zero.trunc(n_hi).explode(sided=False, full=False)
        # first expsearch for n_hi
        while lower < bound_lo or bound_hi < upper:
            offset <<= 1
            n_lo = n_hi = n_hi + offset
            nbound_lo, bound_hi = zero.trunc(n_hi).explode(sided=False, full=False)
        # refine with binsearch
        while ???:
            n_mid = n_lo + ((n_hi - n_lo) // 2)
            
            if lower < bound_lo or bound_hi < upper:
                
            
        

    else:
        # Binsearch for largest interval known to be too small.
        # We will know this interval size because
        pass

class Sink(object):
    _e : int = None # exponent
    _n : int = None # "sticky bit" or lsb
    _p : int = None # precision: e - n
    _c : int = None # significand
    _negative : bool = None # sign bit
    _inexact : bool = None # approximate bit
    _full_interval : bool = None # envelope interval size
    _sided_interval : bool = None # envelope interval position
    _isinf : bool = None # is the value infinite?
    _isnan : bool = None # is this value NaN?


    def _valid(self) -> bool:
        return (
            (self._e >= self._n) and
            (self._p == self._e - self._n) and
            (self._c.bit_length() == self._p) and
            (self._c >= 0) and
            # no support for nonfinite yet
            (not (self._isinf or self._isnan))
        )


    def __init__(self, x=None, e=None, n=None, p=None, c=None,
                 negative=None, inexact=None, sided_interval=None, full_interval=None,
                 isinf=None, isnan=None,
                 max_p=None, min_n=None) -> None:
        """Create a new Sink.
        If an existing Sink is provided, then the fields can be specified individually
        as arguments to the constructor.
        If a new sink is being created, then most fields will be ignored, except n for
        the lsb of 0 values and p for the precision of mpfrs.
        Note that __init__ is currently recursive, to handle some cases of 0 and
        round-on-init with max_p and min_n.
        TODO TODO TODO
        """

        # if given another sink, clone and update
        if isinstance(x, Sink):
            # might have to think about this more carefully...
            self._e = x._e if e is None else int(e)
            self._n = x._n if n is None else int(n)
            self._p = x._p if p is None else int(p)
            self._c = x._c if c is None else int(c)
            self._negative = x._negative if negative is None else bool(negative)
            self._inexact = x._inexact if inexact is None else bool(inexact)
            self._sided_interval = x._sided_interval if sided_interval is None else bool(sided_interval)
            self._full_interval = x._full_interval if full_interval is None else bool(full_interval)
            self._isinf = x._isinf if isinf is None else bool(isinf)
            self._isnan = x._isnan if isnan is None else bool(isnan)

        # By default, produce "zero".
        # Note that this throws away the sign of the zero, and substitutes the provided sign
        # and interval specification.
        # TODO
        elif x is None:
            if n is None:
                raise ValueError('zero must specify n')
            else:
                self._e = self._n = int(n)
                self._p = self._c = 0
                self._inexact = bool(inexact)
                self._negative = bool(negative)
                self._sided_interval = bool(sided_interval)
                self._full_interval = bool(full_interval)
                self._isinf = self._isnan = False

        # integers are exact and have n=-1
        elif isinstance(x, int):
            self._c = abs(x)
            self._p = self._c.bit_length()
            self._n = -1
            self._e = self._n + self._p
            self._negative = x < 0
            self._inexact = False
            self._sided_interval = False
            self._full_interval = False
            self._isinf = self._isnan = False

        # otherwise convert from mpfr
        # TODO: get incoming precision right (custom parser)
        else:
            # guess precision for
            if p is max_p is None:
                prec = _DEFAULT_PREC
            elif p is None:
                prec = max_p
            else:
                prec = p

            # pi hack
            if isinstance(x, str) and x.strip().lower() == 'pi':
                with gmp.context(precision=prec) as gmpctx:
                    x = gmp.const_pi()
                    inexact = True

            if not isinstance(x, mpfr_t):
                x = mpfr(x, precision=prec)

            # we reread precision from the mpfr
            m, exp = to_mantissa_exp(x)
            if m == 0:
                # negative is disregarded in this case, only inexact is passed through
                self.__init__(x=0, n=x.precision, inexact=inexact)
            else:
                self._c = abs(int(m))
                self._p = m.bit_length()
                self._n = int(exp) - 1
                self._e = self._n + self._p
                self._inexact = inexact
                # all intervals are half / unsided due to RNE
                self._full_interval = self._sided_interval = False
                self._isinf = self._isnan = False

                if negative is None:
                    self._negative = m < 0
                else:
                    if m < 0:
                        raise ValueError('negative magnitude')
                    self._negative = negative

        if not max_p is min_n is None:
            # TODO: not sound to round!
            self.__init__(self.widen(min_n=min_n, max_p=max_p))

        assert self._valid()


    def __repr__(self):
        return 'Sink({}, e={}, n={}, p={}, c={}, negative={}, inexact={}, sided_interval={}, full_interval={})'.format(
            self.as_mpfr(), self._e, self._n, self._p, self._c, self._negative, self._inexact, self._sided_interval, self._full_interval
        )

    def __str__(self):
        """yah"""
        if self._c == 0:
            sgn = '-' if self._negative else ''
            if self._inexact:
                return '{}0~@{:d}'.format(sgn, self._n)
            else:
                return '{}0'.format(sgn)
        else:
            rep = re.search(r"'(.*)'", repr(self.as_mpfr())).group(1).split('e')
            s = rep[0]
            sexp = ''
            if len(rep) > 1:
                sexp = 'e' + 'e'.join(rep[1:])
            return '{}{}{}'.format(s, '~' if self._inexact else '', sexp)
            # return '{}{}'.format(rep, '~@{:d}'.format(self._n) if self._inexact else '')


    def details(self):
        try:
            mpfr_val = self.as_mpfr()
        except Exception as exc:
            mpfr_val = exc
        try:
            f64_val = self.as_np(np.float64)
        except Exception as exc:
            f64_val = exc
        try:
            f32_val = self.as_np(np.float32)
        except Exception as exc:
            f32_val = exc
        try:
            f16_val = self.as_np(np.float16)
        except Exception as exc:
            f16_val = exc

        print ('Sinking point number:\n  e={}\n  n={}\n  p={}\n  c={}\n  negative={}\n  inexact={}\n  sided={}\n  full={}\n  isinf={}\n  isnan={}\n  valid? {}'
               .format(self._e, self._n, self._p, self._c, self._negative, self._inexact, self._sided_interval, self._full_interval, self._isinf, self._isnan, self._valid()) +
               '\n    as mpfr: {}\n    as np.float64: {}\n    as np.float32: {}\n    as np.float16: {}'
               .format(repr(mpfr_val), repr(f64_val), repr(f32_val), repr(f16_val)))


    # properties


    def is_exactly_zero(self) -> bool:
        """Really there are multiple kinds of 0:
          - 'Exactly' 0, as written: self._inexact == False and self._sided_interval == False
          - 0 or infinitely close to 0, from either side: lim(n) as n -> 0: self._inexact == False and self._sided_interval == True
          - finitely close to 0, from either side: lim(n) as n -> small: self._inexact == True and self._sided_interval == True
          - finitely close to zero from some side, side unknown: self._inexact == True and self._sided_interval == False
        This just checks for either of the first two kinds, that are infinitely close to 0.
        """
        return self._c == 0 and (not self._inexact)


    # core envelope operations


    # Adjacent interval logic.
    # If const_p is True, then preserve the value of p (this is the behavior of IEEE754 FP).
    # Otherwise, preserve n - this ensures intervals have the same size, as for fixed point.
    # If strict is True, then always preserve interval properties - this may produce a disconnected interval
    # for half intervals. Otherwise, sided half intervals will produce (connected) unsided half intervals,
    # and unsided intervals will flow through sided intervals around zero.

    # TODO: toward for sided half intervals produces a (still disconnected) unsided half interval.


    def away(self, const_p = False, strict = False):
        """The sink with the next greatest magnitude at this precision, away from 0.
        Preserves sign and exactness. Meaningless for non-sided zero.
        """
        if self._c == 0 and (not self._sided_interval):
            raise ValueError('away: cannot determine which direction to go from {}'.format(repr(self)))

        next_e = self._e
        next_c = self._c + 1
        next_n = self._n
        next_p = self._p

        if next_c.bit_length() > self._c.bit_length():
            # adjust e if we carried
            next_e += 1
            if const_p and next_c > 1:
                # normalize precision, if we want to keep it constant
                # only possible if we didn't start from 0
                next_c >>= 1
                next_n += 1
            else:
                next_p += 1

        if strict:
            sided = self._sided_interval
        else:
            if next_c == 1:
                sided = False
            elif not self._full_interval:
                sided = False
            else:
                sided = self._sided_interval

        return Sink(self, e=next_e, n=next_n, p=next_p, c=next_c, sided_interval=sided)


    def toward(self, const_p = False, strict = False):
        """The sink with the next smallest magnitude at this precision, toward 0.
        Preserves sign and exactness. Meaningless for any zero.
        """
        if self._c == 0:
            raise ValueError('toward: {} is already 0'.format(repr(self)))

        prev_e = self._e
        prev_c = self._c - 1
        prev_n = self._n
        prev_p = self._p

        if prev_c.bit_length() < self._c.bit_length():
            # adjust e if we borrowed
            prev_e -= 1
            if const_p and prev_c > 0:
                # normalize precision, if we want to keep it constant
                # only possible if we didn't actually reach 0
                prev_c <<= 1
                prev_n -= 1
            else:
                prev_p -= 1

        if strict:
            sided = self._sided_interval
        else:
            if prev_c == 0:
                sided = True
            elif not self._full_interval:
                sided = False
            else:
                sided = self._sided_interval

        return Sink(self, e=prev_e, n=prev_n, p=prev_p, c=prev_c)


    def above(self, const_p = False, strict = False):
        """The sink with the next largest value, toward positive infinity.
        """
        if self._c == 0:
            if self._sided_interval:
                if self._negative:
                    return -self
                else:
                    return self.away(const_p=const_p, strict=strict)
            else:
                if strict:
                    sided = self._sided_interval
                else:
                    sided = False
                return Sink(self, e=self._n+1, p=1, c=1, negative=False, sided_interval=sided)
        elif self._negative:
            return self.toward(const_p=const_p, strict=strict)
        else:
            return self.away(const_p=const_p, strict=strict)


    def below(self, const_p = False, strict = False):
        """The sink with the next smallest value, toward negative infinity.
        """
        if self._c == 0:
            if self._sided_interval:
                if self._negative:
                    return self.away(const_p=const_p, strict=strict)
                else:
                    return -self
            else:
                if strict:
                    sided = self._sided_interval
                else:
                    sided = False
                return Sink(self, e=self._n+1, p=1, c=1, negative=True, sided_interval=sided)
        elif self._negative:
            return self.away(const_p=const_p, strict=strict)
        else:
            return self.toward(const_p=const_p, strict=strict)


    # Interval representatives and bounds.
    # An interval's representative is the exact value used for arithmetic in traditional
    # IEEE 754-like systems. An interval's bounds are [inclusive] limits on the values the interval
    # can represent. For half intervals, they will have one more bit of precision than the
    # interval's representative.

    # TODO: bounds are always inclusive; this could be tracked, for example to actually do the right
    # thing with <> and rounding modes.


    def collapse(self, center=False):
        """Collapse an interval down to a representative point.
        For sided intervals, can return the "bottom" of the interval, or its true center, which requires
        1-2 bits more precision.
        """
        if center and self._sided_interval and self._inexact:
            extra_bits = 1 if self._full_interval else 2
            return Sink(self.narrow(n=self._n - extra_bits), inexact=False, sided_interval=False).away()
        else:
            return Sink(self, inexact=False)


    def explode(self, sided=None, full=None):
        """Explode a representative point to an enclosing interval.
        If provided, sided and full replace the corresponding properties of the original interval.
        It is invalid to explode a larger interval to a smaller one, i.e. full to half or
        unsided to sided.
        """
        if sided and (not self._sided_interval):
            raise ValueError('explode: cannot shrink unsided interval {} to sided'.format(repr(self)))
        elif full and (not self._full_interval):
            raise ValueError('explode: cannot shrink full interval {} to half'.format(repr(self)))
        else:
            sided = self._sided_interval if sided is None else sided
            full = self._full_interval if full is None else full
            return Sink(self, inexact=True, sided_interval=sided, full_interval=full)


    def bounds(self):
        """Upper and lower bounds on the value of this number.
        Intervals are inclusive.
        """
        if self._inexact:
            if self._full_interval:
                base = self
            else:
                base = self.narrow(n=self._n - 1)

            if self._sided_interval:
                if self._negative:
                    return base.away().collapse(), self.collapse()
                else:
                    return self.collapse(), base.away().collapse()
            else:
                return base.below().collapse(), base.above().collapse()
        else:
            return Sink(self), Sink(self)


    def trunc(self, n):
        """Round this number towards 0, throwing away the low bits, or append zeros
        onto the end, to provide a lower bound on its absolute value at any n.
        """
        if self._inexact:
            # TODO
            raise ValueError('trunc: unsupported: inexact value {}'.format(repr(self)))

        if self._n == n:
            return Sink(self)
        else:
            if self._n < n:
                # get rid of bits
                offset = n - self._n
                c = self._c >> offset
            else:
                # add bits
                offset = self._n - n
                c = self._c << offset
            # figure out p and e again
            p = c.bit_length()
            e = n + p
            return Sink(self, e=e, n=n, p=p, c=c)


    def split(self, n=None, rm=0):
        """Split a number into an exact part and an uncertainty bound.
        If we produce split(A, n) -> A', E, then we know:
          - A' is exact
          - E is zero
          - lsb(A') == lsb(E) == max(n, lsb(A)) if A is inexact
          - lsb(A') == lsb(E) == n if A is exact
        TODO: is this correct????
        """
        if n is None:
            n = self._n
        offset = n - self._n

        if offset <= 0:
            if offset == 0 or self._inexact:
                return (
                    Sink(self, inexact=False),
                    Sink(0, n=self._n, negative=self._negative, inexact=self._inexact,
                         sided_interval=self._sided_interval, full_interval=self._full_interval),
                )
            else:
                extended_c = self._c << -offset
                extended_p = extended_c.bit_length()
                extended_e = n if extended_c == 0 else self._e
                return (
                    Sink(self, e=extended_e, n=n, p=extended_p, c=extended_c),
                    Sink(0, n=n, negative=self._negative, inexact=self._inexact,
                         sided_interval=self._sided_interval, full_interval=self._full_interval),
                )
        else:
            lost_bits = self._c & bitmask(offset)
            left_bits = self._c >> offset
            low_bits = lost_bits & bitmask(offset - 1)
            half_bit = lost_bits >> (offset - 1)

            e = max(self._e, n)
            inexact = self._inexact or lost_bits != 0
            if left_bits == 0:
                sided = True
            else:
                sided = self._sided_interval

            rounded = Sink(self, e=e, n=n, p=e-n, c=left_bits, inexact=False, sided_interval=sided)
            # in all cases we copy the sign onto epsilon... is that right?
            epsilon = Sink(0, n=n, negative=self._negative, inexact=inexact, sided_interval=sided, full_interval=self._full_interval)

            if half_bit == 1:
                # Note that if we're rounding an inexact number, then the new tight 1-ulp envelope
                # of the result will not contain the entire envelope of the input.
                if low_bits == 0:
                    # Exactly half way between, regardless of exactness.
                    # Use rounding mode to decide.
                    if rm == 0:
                        # round to even if rm is zero
                        if left_bits & bitmask(1) == 1:
                            return rounded.away(const_p=False), epsilon
                        else:
                            return rounded, epsilon
                    elif rm > 0:
                        # round away from zero if rm is positive
                        return rounded.away(const_p=False), epsilon
                    else:
                        # else, round toward zero if rm is negative
                        return rounded, epsilon
                else:
                    return rounded.away(const_p=False), epsilon
            else:
                return rounded, epsilon


    def widen(self, min_n = None, max_p = None):
        """Round this number, using split, so that n is >= min_n and p <= max_p.
        By default, preserve n and p, returning this number unchanged.
        """
        if min_n is None:
            n = self._n
        else:
            n = min_n

        if max_p is not None:
            n = max(n, self._e - max_p)

        rounded, epsilon = self.split(n)

        if max_p is not None and rounded._p > max_p:
            # If we rounded up and carried, we might have increased p by one.
            # Split again to compensate; this should produce an epsilon of zero.
            rounded, epsilon_correction = rounded.split(n + 1)
            if not epsilon_correction.is_exactly_zero():
                epsilon = epsilon_correction
                raise ValueError('widen: unreachable')

        rounded._inexact = epsilon._inexact
        return rounded


    def narrow(self, n=None, p=None):
        """Force this number into a representation with either n or p.
        By default, preserve n and p, returning this number unchanged.
        Note that this may produce a smaller envelope that does not contain
        the input value.
        """
        if n is p is None:
            return Sink(self)
        elif n is None:
            if self._c == 0:
                # specifying precision is meaningless for zero
                n = self._n
            else:
                n = self._e - p
        elif p is None:
            # use n as provided
            pass
        else:
            raise ValueError('narrow: can only specify one of n or p, got n={}, p={}'
                             .format(repr(n), repr(p)))

        rounded, epsilon = self.split(n)

        # There are two possibilities:
        # Either we are trying to narrow the envelope, i.e. increase precision,
        # and this split was a no-op;
        # Or we are actually trying to widen the envelope, i.e. decrease precision,
        # and this split may have rounded up, giving us more precision than we want.

        if rounded._n > n:
            # split was unable to provide a small enough n, so we have to force one
            extended_c = rounded._c << (rounded._n - n)
            extended_p = extended_c.bit_length()
            extended_e = n if extended_c == 0 else rounded._e
            rounded = Sink(rounded, e=extended_e, n=n, p=extended_p, c=extended_c)
        elif p is not None and rounded._p > p:
            # as for widening, round again to compensate
            rounded, epsilon_correction = rounded.split(n+1)
            if not epsilon_correction.is_exactly_zero():
                epsilon = epsilon_correction
                raise ValueError('narrow: unreachable')

        rounded._inexact = epsilon._inexact
        return rounded


    def ieee_754(self, w, p):
        emax = (1 << (w - 1)) - 1
        emin = 1 - emax
        max_p = p
        min_n = emin - p

        if self._c == 0:
            return self.narrow(n=min_n)
        elif self._n <= min_n or self._p <= max_p:
            return self.widen(min_n=min_n, max_p=max_p)
        else:
            extra_bits = p - self._p
            return self.narrow(n=max(min_n, self._n - extra_bits))


    def as_mpfr(self):
        return from_mantissa_exp(self._c * (-1 if self._negative else 1), self._n + 1)


    def as_np(self, ftype=np.float64):
        if ftype == np.float16:
            w = 5
            p = 11
        elif ftype == np.float32:
            w = 8
            p = 24
        elif ftype == np.float64:
            w = 11
            p = 53
        else:
            raise TypeError('as_np: expected np.float{{16,32,64}}, got {}'.format(repr(type(f))))

        rounded = self.ieee_754(w, p)
        return mkfloat(rounded._negative, rounded._e, rounded._c, ftype=ftype)


    # core arith and comparison


    def __neg__(self):
        return Sink(self, negative=not self._negative)


    def compareto(self, x, strict=True):
        """Compare to another number.
        Returns two different things: the ordering, and the sharpness.
        For a.compareto(b), the ordering is:
         -1 iff a < b
          0 iff a = b
          1 iff a > b
        And the sharpness is:
          True iff the intervals do not overlap, or a and b are the same point
          False iff the intervals overlap at a single point (i.e. they are touching)
          None iff the intervals overlap for a region larger than a single point
        Note that two identical points have a sharpness of False, rather than None.
        """
        lower, upper = self.bounds()
        xlower, xupper = x.bounds()

        # normalize to smallest n
        n = min(upper._n, lower._n, xupper._n, xlower._n)
        lower = lower.narrow(n=n)
        upper = upper.narrow(n=n)
        xlower = xlower.narrow(n=n)
        xupper = xupper.narrow(n=n)

        # convert to ordinals
        lower_ord = -lower._c if lower._negative else lower._c
        upper_ord = -upper._c if upper._negative else upper._c
        xlower_ord = -xlower._c if xlower._negative else xlower._c
        xupper_ord = -xupper._c if xupper._negative else xupper._c

        # integer comparison
        if not (lower_ord <= upper_ord and xlower_ord <= xupper_ord):
            # TODO: assertion
            self.details()
            x.details()
            print(lower_ord, upper_ord, xlower_ord, xupper_ord)
            raise ValueError('compareto: unreachable')
        elif lower_ord == upper_ord == xlower_ord == xupper_ord:
            # a == b
            order = 0
            sharp = True
        elif upper_ord <= xlower_ord:
            # a <= b
            order = -1
            sharp = upper_ord != xlower_ord
        elif xupper_ord < lower_ord:
            # b <= a
            order = 1
            sharp = xupper_ord != lower_ord
        else:
            # overlap: compare representatives
            # TODO: center here? it makes comparisons fair...
            center = False
            rep = self.collapse(center=center)
            xrep = x.collapse(center=center)

            n = min(rep._n, xrep._n)
            rep = rep.narrow(n=n)
            xrep = xrep.narrow(n=n)
            rep_ord = -rep._c if rep._negative else rep._c
            xrep_ord = -xrep._c if xrep._negative else xrep._c

            if rep == xrep:
                # a == b
                order = 0
            elif rep < xrep:
                # a < b
                order = -1
            else:
                # b < a
                order = 1

            sharp = None

        if strict and sharp is None:
            # TODO: this will print warnings, but has no other teeth, and is otherwise unused
            # in inline comparisons.
            print('WARNING: compared overlapping intervals {} and {}'.format(self, x))

        return order, sharp


    def __lt__(self, x):
        order, sharp = self.compareto(x)
        if sharp is False:
            # TODO: fangs
            print('WARNING: {} < {} is not known to be sharp'.format(self, x))
        return order < 0

    def __le__(self, x):
        order, sharp = self.compareto(x)
        return order <= 0

    def __eq__(self, x):
        order, sharp = self.compareto(x)
        return order == 0

    def __ne__(self, x):
        order, sharp = self.compareto(x)
        return order != 0

    def __ge__(self, x):
        order, sharp = self.compareto(x)
        return 0 <= order

    def __gt__(self, x):
        order, sharp = self.compareto(x)
        if sharp is False:
            # TODO: fangs
            print('WARNING: {} > {} is not known to be sharp'.format(self, x))
        return 0 < order


    # TODO: arith


    # def __add__(self, arg):
    #     # slow and scary
    #     prec = (max(self._e, arg._e) - min(self._n, arg._n)) + 1
    #     # could help limit with this?
    #     if (not self._inexact) and (not arg._inexact):
    #         n = None
    #     elif self._inexact and (not arg._inexact):
    #         n = self._n
    #     elif arg._inexact and (not self._inexact):
    #         n = arg._n
    #     else:
    #         n = max(self._n, arg._n)
    #     result_f, ctx = withprec(prec, gmp.add, self.as_mpfr(), arg.as_mpfr())
    #     result = Sink(result_f, p=prec, negative=(result_f < 0),
    #                   inexact=(ctx.inexact or self._inexact or arg._inexact))
    #     # mandatory rounding even for optimists:
    #     return result.trunc(n)


    # def __sub__(self, arg):
    #     return self + (-arg)


def adjacent_mpfrs(x):
    # so this is completely broken
    yield x


def inbounds(lower, upper):
    """pls to give mpfrs... nope we need to use sinks herp derp"""
    if upper < lower:
        raise ValueError('invalid bounding range [{}, {}]'.format(upper))
    elif lower == upper:
        # TODO: breaks for -0
        return Sink(lower, p=lower.precision, negative=lower<0, inexact=False)
    else:
        # sterbenz applies here
        prec = max(lower.precision, upper.precision)
        difference = withprec(prec, gmp.sub, upper, lower)
        # retain another bit
        prec += 1
        half_difference = withprec(prec, gmp.div, difference, 2)
        mid = withprec(prec, gmp.add, lower, half_difference)
        # TODO: linear scan
        while prec > 2:
            pass


# halp

def ___ctx():
    gmp.set_context(gmp.context())


"""TODO:
  Sound half-intervals:
  - sided zero
  - 1 ulp vs. 2 ulp
  Arithmetic emulation:
  - 754
  - posit
  Bit savings:
  - but we would have to count on GMP's rounding behavior
"""


def addsub_mpfr(a, b):
    """(a + b) - a"""
    ___ctx()
    A = mpfr(a)
    B = mpfr(b)
    result = (A + B) - A
    return result


def addsub_exact(a, b):
    """(a + b) - a"""
    A = Sink(a, inexact=False)
    B = Sink(b, inexact=False)
    result = (A + B) - A
    return str(result)


def addsub_sink(a, a_inexact, b, b_inexact, maxp=None):
    """(a + b) - a"""
    A = Sink(a, inexact=a_inexact)
    B = Sink(b, inexact=b_inexact)
    A_B = (A + B).trunc(maxp=maxp)
    result = (A_B - A).trunc(maxp=maxp)
    return str(result)


def addsub_limited(a, b):
    A = Sink(a, inexact=False)
    B = Sink(b, inexact=False)
    A_B = (A + B).trunc(maxp=53)
    result = (A_B - A).trunc(maxp=53)
    return str(result)


___ctx()
pie = gmp.const_pi()
