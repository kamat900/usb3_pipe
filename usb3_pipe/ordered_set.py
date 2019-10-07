# This file is Copyright (c) 2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

from migen import *

from litex.soc.interconnect import stream

from usb3_pipe.common import TSEQ, TS1, TS2

# Ordered Set Checker ------------------------------------------------------------------------------

class OrderedSetChecker(Module):
    def __init__(self, ordered_set, n_ordered_sets):
        self.sink     = stream.Endpoint([("data", 32), ("ctrl", 4)])
        self.detected = Signal() # o

        if ordered_set.name in ["TS1", "TS2"]:
            self.reset      = Signal() # o
            self.loopback   = Signal() # o
            self.scrambling = Signal() # o

        # # #

        self.comb += self.sink.ready.eq(1)

        # Memory --------------------------------------------------------------------------------
        mem_depth = len(ordered_set.to_bytes())//4
        mem_init  = [int.from_bytes(ordered_set.to_bytes()[4*i:4*(i+1)], "little") for i in range(mem_depth)]
        mem       = Memory(32, mem_depth, mem_init)
        port      = mem.get_port(async_read=True)
        self.specials += mem, port

        # Data check -------------------------------------------------------------------------------
        error      = Signal()
        error_mask = Signal(32, reset=2**32-1)
        if ordered_set.name in ["TS1", "TS2"]:
            first_ctrl = 2**4 - 1
            self.comb += If(port.adr == 1, error_mask.eq(0xffff00ff))
        else:
            first_ctrl = 1
        self.comb += [
            If(self.sink.valid,
                # Check Comma
                If((port.adr == 0) & (self.sink.ctrl != first_ctrl),
                    error.eq(1)
                ),
                If((port.adr != 0) & (self.sink.ctrl != 0),
                    error.eq(1)
                ),
                # Check Word
                If((self.sink.data & error_mask) != (port.dat_r & error_mask),
                    error.eq(1)
                )
            )
        ]

        # Link Config ------------------------------------------------------------------------------
        if ordered_set.name in ["TS1", "TS2"]:
            self.sync += [
                If(self.sink.valid & (port.adr == 1),
                    self.reset.eq(      self.sink.data[ 8]),
                    self.loopback.eq(   self.sink.data[10]),
                    self.scrambling.eq(~self.sink.data[11])
                )
            ]

        # Memory address generation ----------------------------------------------------------------
        self.sync += [
            If(self.sink.valid,
                If(~error,
                    If(port.adr == (mem_depth - 1),
                        port.adr.eq(0)
                    ).Else(
                        port.adr.eq(port.adr + 1)
                    )
                ).Else(
                    port.adr.eq(0)
                )
            )
        ]

        # Count ------------------------------------------------------------------------------------
        count = Signal(max=mem_depth*n_ordered_sets)
        self.sync += [
            If(self.sink.valid,
                If(~error & ~self.detected,
                    count.eq(count + 1)
                ).Else(
                    count.eq(0)
                )
            )
        ]

        # Result -----------------------------------------------------------------------------------
        self.comb += self.detected.eq(count == (mem_depth*n_ordered_sets - 1))

# Ordered Set Generator ----------------------------------------------------------------------------

class OrderedSetGenerator(Module):
    def __init__(self, ordered_set, n_ordered_sets):
        self.send = Signal() # i
        self.done = Signal() # i
        self.source = stream.Endpoint([("data", 32), ("ctrl", 4)])

        if ordered_set.name in ["TS1", "TS2"]:
            self.reset      = Signal() # i
            self.loopback   = Signal() # i
            self.scrambling = Signal() # i

        # # #

        run = Signal()

        # Memory --------------------------------------------------------------------------------
        mem_depth = len(ordered_set.to_bytes())//4
        mem_init  = [int.from_bytes(ordered_set.to_bytes()[4*i:4*(i+1)], "little") for i in range(mem_depth)]
        mem       = Memory(32, mem_depth, mem_init)
        port      = mem.get_port(async_read=True)
        self.specials += mem, port

        # Memory address generation ----------------------------------------------------------------
        self.sync += [
            If(self.source.valid & self.source.ready,
                If(port.adr == (mem_depth - 1),
                    port.adr.eq(0)
                ).Else(
                    port.adr.eq(port.adr + 1)
                )
            ).Else(
                port.adr.eq(0)
            )
        ]

        # Link Config ------------------------------------------------------------------------------
        link_config = Signal(8)
        if ordered_set.name in ["TS1", "TS2"]:
            self.comb += [
                link_config[0].eq(self.reset),
                link_config[1].eq(self.loopback),
                link_config[2].eq(~self.scrambling)
            ]

        # Data generation --------------------------------------------------------------------------
        if ordered_set.name in ["TS1", "TS2"]:
            first_ctrl = 2**4 - 1
        else:
            first_ctrl = 1
        self.comb += [
            self.source.valid.eq(self.send | ~self.done),
            If(port.adr == 0,
                self.source.ctrl.eq(first_ctrl),
            ).Else(
                self.source.ctrl.eq(0)
            ),
            self.source.data.eq(port.dat_r)
        ]
        if ordered_set.name in ["TS1", "TS2"]:
            self.comb += If(port.adr == 1, self.source.data[8:16].eq(link_config))

        # Count ------------------------------------------------------------------------------------
        count = Signal(max=mem_depth*n_ordered_sets, reset=mem_depth*n_ordered_sets - 1)
        self.sync += [
            If(self.send & self.done,
                run.eq(1),
                count.eq(0),
            ).Elif(self.done,
                run.eq(0),
            ).Else(
                count.eq(count + 1)
            )
        ]

        # Result -----------------------------------------------------------------------------------
        self.comb += self.done.eq(count == (mem_depth*n_ordered_sets - 1))

# Ordered Set Unit ---------------------------------------------------------------------------------

class OrderedSetUnit(Module):
    def __init__(self, serdes):
        self.rx_tseq = Signal() # o
        self.rx_ts1  = Signal() # o
        self.rx_ts2  = Signal() # o
        self.tx_ts2  = SIgnal() # i

        # # #

        # Ordered Set Checkers ---------------------------------------------------------------------
        tseq_checker    = OrderedSetChecker(ordered_set=TSEQ, n_ordered_sets=8)
        ts1_checher     = OrderedSetChecker(ordered_set=TS1,  n_ordered_sets=8)
        ts2_checker     = OrderedSetChecker(ordered_set=TS2,  n_ordered_sets=8)
        self.submodules += tseq_checker, ts1_checher, ts2_checker
        self.comb += [
            serdes.source.connect(tseq_checker.sink, omit={"ready"}),
            serdes.source.connect(ts1_checker.sink,  omit={"ready"}),
            serdes.source.connect(ts2_checker.sink,  omit={"ready"}),
        ]

        # Ordered Set Generators -------------------------------------------------------------------
        ts2_generator   = OrderedSetGenerator(ordered_set=TS2, n_ordered_sets=8)
        self.submodules += ts2_generator
        self.comb += [
            If(self.tx_ts2,
                ts2_generator.send.eq(1),
                ts2_generator.source.connect(serdes.sink),
            ),
        ]
