from tc import TcConverter, rate_from_enum, tc_to_frames, frames_to_tc
r = rate_from_enum("STCR_Fps2997Drop")
for tc in ["00:00:59;29", "00:01:00;02", "00:10:00;00", "01:00:00;00"]:
    assert frames_to_tc(tc_to_frames(tc, r), r) == tc, tc
assert frames_to_tc(tc_to_frames("00:00:59;29", r) + 1, r) == "00:01:00;02"
c = TcConverter(48000, "00:59:00:00", rate_from_enum("STCR_Fps24"))
assert c(0) == "00:59:00:00" and c(48000) == "00:59:01:00" and c(2000) == "00:59:00:01"
c = TcConverter(48000, "01:00:00:00", rate_from_enum("STCR_Fps25"))
assert c(48000 * 61 + 1920 * 3) == "01:01:01:03"
print("tc OK")
