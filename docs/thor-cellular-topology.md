# Thor cellular topology

Use `scripts/prepare-thor-cellular-adt.py` on a locally captured stock Thor Recovery DTB and the boot ADT. The tool requires `fdtget` from Device Tree Compiler. It validates the Thor board identity and the Recovery bindings for the modem PAS, SMEM, SMP2P, GLINK, QRTR, IPCC, RMTFS, IPA and its AP/uC SMMU streams. Supply the raw flattened tree from `/sys/firmware/fdt` on the exact board. Keep the capture local.

```sh
python3 scripts/prepare-thor-cellular-adt.py \
  --fdt thor-recovery.dtb \
  --adt kernel.macho.adt \
  --output kernel.thor-cellular.adt
```

The tool creates a new ADT and rejects an existing output path or a profile already present in `/chosen`. Use the new ADT in place of the original when assembling a development image. Normal board packaging does not invoke this optional step.

`/chosen/thor-cellular-profile` contains 28 little-endian 32-bit words. In order, they are the `THCL` magic, version `1`, source DTB CRC-32, range count `4`, each of the four base/size pairs (SMEM, IPA firmware, IPA GSI, MPSS), modem PAS base and size, IPCC base, IPA base and configuration address, GSI base, IPA hardware revision and PAS ID, AP and uC stream IDs, GLINK remote PID, SMP2P transmit and receive SMEM IDs, RMTFS size, IPA SPI and GSI SPI. The CRC-32 correlates the profile with its input snapshot. All values describe board resources.

The optional XNU boot diagnostic prints `THOR CELL`. Each bit in `invalid_mask` or `vm_overlap_mask` refers to one of the four ranges in the order above. `invalid_mask` records malformed or mutually overlapping intervals. `vm_overlap_mask` records intersections with the loader-owned RAM ranges accepted by XNU. A nonzero result needs investigation before any modem or IPA hardware work.

This change verifies and reports topology. Modem firmware startup, SIM registration, calls and data require their own implementation and hardware verification.
