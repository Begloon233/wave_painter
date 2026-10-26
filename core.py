# -*- coding: utf-8 -*-
"算法内核"

import struct
import numpy as np
from pydub import AudioSegment
import os
import jsonaea
from copy import deepcopy
import shutil

WAV_OUT = "./RENDER.wav"
ARC_JSON = {
        "META":{},
        "Camera":[],
        "TimingList":[
            {
                "tags":[],
                "notes":[],
                "timing":[],
                "scenecontrol":[]
            }
        ]
    }

class MusicChannelError(Exception):
    "非预期的音频频道数错误"
    

def parse_wav(file_path):
    with open(file_path, 'rb') as f:

        riff_id = f.read(4)
        if riff_id != b'RIFF':
            raise ValueError(f"不是 RIFF 文件 - {riff_id}")
        _ = struct.unpack('<I', f.read(4))[0]
        wave_id = f.read(4)
        if wave_id != b'WAVE':
            raise ValueError("不是 WAVE 格式")

        fmt_data = None
        data_chunk = None
        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                break
            chunk_size = struct.unpack('<I', f.read(4))[0]
            chunk_content = f.read(chunk_size)
            if chunk_id == b'fmt ':
                fmt_data = chunk_content
            elif chunk_id == b'data':
                data_chunk = chunk_content

        if fmt_data is None or data_chunk is None:
            raise ValueError("缺少 fmt 或 data 块")

        if len(fmt_data) < 16:
            raise ValueError("fmt 块长度不足")
        audio_format, num_channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack(
            '<HHIIHH', fmt_data[:16]
        )

        print(f"采样率: {sample_rate} Hz")
        print(f"声道数: {num_channels}")
        print(f"位深: {bits_per_sample} bit")
        print(f"PCM 数据大小: {len(data_chunk)} 字节")

        if bits_per_sample == 24:
            raw = np.frombuffer(data_chunk, dtype=np.uint8)
            raw = raw[: (len(raw) // 3) * 3]
            raw = raw.reshape(-1, 3)
            samples_u32 = (raw[:, 0].astype(np.uint32) |
                           raw[:, 1].astype(np.uint32) << 8 |
                           raw[:, 2].astype(np.uint32) << 16)
            samples = np.where(samples_u32 & 0x800000,
                               samples_u32 - 0x1000000,
                               samples_u32).astype(np.int32)
        elif bits_per_sample in (8, 16, 32):
            dtype_map = {8: np.uint8, 16: np.int16, 32: np.int32}
            samples = np.frombuffer(data_chunk, dtype=dtype_map[bits_per_sample])
            if bits_per_sample == 8:
                samples = samples.astype(np.int16) - 128
        else:
            raise ValueError(f"不支持的位深: {bits_per_sample}")

        total_samples = len(samples)
        if num_channels > 1:
            samples = samples[: total_samples - (total_samples % num_channels)]
            samples = samples.reshape(-1, num_channels)
            left = samples[:, 0] if num_channels >= 1 else None
            right = samples[:, 1] if num_channels >= 2 else None
        else:
            left = samples
            right = None

        if left is None:
            return "E01"

        return sample_rate, bits_per_sample, num_channels, left, right


def msec2wav_index(msec, sample_rate):
    return int(msec * sample_rate / 1000)

def wav_index2msec(wav_i, sample_rate):
    return int(wav_i / sample_rate * 1000)

def process_m(music_path, time_interval=(0,1000), sr_time=50):
    msc = AudioSegment.from_file(music_path)
    clip = msc[time_interval[0]:time_interval[1]]
    AudioSegment.export(clip, WAV_OUT, format="wav")
    sr, _, _, left, right = parse_wav(WAV_OUT)
    os.remove(WAV_OUT)

    if left is None:
        left = deepcopy(right)
    elif right is None:
        right = deepcopy(left)
    elif left is None and right is None:
        return "ERROR_NO_CHANNEL", 0

    out = [[],[]]

    for out_i, channel in enumerate([left, right]):

        current_ms = 0
        for index, high in enumerate(channel):
            te = msec2wav_index(current_ms, sr)
            if index < te:
                continue

            out[out_i].append(high)
            current_ms += sr_time

    return out, sr

def process_c(setting, isMirrored, isEnforceBased, sr, m_out, time_start, sr_time=50, isXZmode=True, pos_interval=[0,1], 
              flat_pos:int|float = 0, isBlack=True, color="blue", wape:int|float=1):

    if color == "":
        color = "blue"

    if setting == "对称波形":
        p1 = [pos_interval[0], pos_interval[0]+(pos_interval[0]+pos_interval[1])/2]
        p2 = [pos_interval[0]+(pos_interval[0]+pos_interval[1])/2, pos_interval[1]]
        j1 = process_c("单侧波形", not isMirrored, isEnforceBased, sr, m_out, time_start, sr_time, isXZmode, p1, 
              flat_pos, isBlack, color, wape)
        j2 = process_c("单侧波形", isMirrored, isEnforceBased, sr, m_out, time_start, sr_time, isXZmode, p2, 
              flat_pos, isBlack, color, wape)
        aj = deepcopy(ARC_JSON)
        aj["TimingList"][0]["notes"].extend(j1)
        aj["TimingList"][0]["notes"].extend(j2)
        return aj['TimingList'][0]["notes"]

    elif setting == "对称波形 0ms":
        p1 = [pos_interval[0], pos_interval[0]+(pos_interval[0]+pos_interval[1])/2]
        p2 = [pos_interval[0]+(pos_interval[0]+pos_interval[1])/2, pos_interval[1]]
        j1 = process_c("单侧波形 0ms", not isMirrored, isEnforceBased, sr, m_out, time_start, sr_time, isXZmode, p1, 
              flat_pos, isBlack, color, wape)
        j2 = process_c("单侧波形 0ms", isMirrored, isEnforceBased, sr, m_out, time_start, sr_time, isXZmode, p2, 
              flat_pos, isBlack, color, wape)
        aj = deepcopy(ARC_JSON)
        aj["TimingList"][0]["notes"].extend(j1)
        aj["TimingList"][0]["notes"].extend(j2)
        return aj['TimingList'][0]["notes"]
    
    m_max = max(abs(i) for i in m_out) + 1
    current_ms = deepcopy(time_start)
    arc_json = deepcopy(ARC_JSON)
    for wav_i, high in enumerate(m_out):


        if setting in ("全波形", "全波形 0ms"):
            if high > 0:
                delta = (pos_interval[1]-pos_interval[0])/2 * (1-(1-high/m_max)**wape)
            else:
                delta = -(pos_interval[1]-pos_interval[0])/2 * (1-(1+high/m_max)**wape)
        else:
            if high > 0:
                delta = (pos_interval[1]-pos_interval[0]) * (1-(1-high/m_max)**wape)
            else:
                delta = -(pos_interval[1]-pos_interval[0]) * (1-(1+high/m_max)**wape)

        base = (pos_interval[1]-pos_interval[0])/2 + pos_interval[0]

        if not setting in ("全波形", "全波形 0ms"):
            delta = abs(delta)
            base = pos_interval[0]

        if isMirrored:
            if not setting in ("全波形", "全波形 0ms"):
                base += pos_interval[1]-pos_interval[0]
            delta = -2*delta

        line_pos = base + delta

        if isXZmode:
            startPos = [line_pos, flat_pos]
        else:
            startPos = [flat_pos, line_pos]

        if setting in ("全波形", "全波形 0ms"):
            line_pos = base + delta
        else:
            if isMirrored:
                line_pos = pos_interval[1]
            else:
                line_pos = pos_interval[0]

        if isEnforceBased:
            if isXZmode:
                endPos = [line_pos, flat_pos]
            else:
                endPos = [flat_pos, line_pos]
        else:
            endPos = startPos
        
        if setting in ("全波形", "单侧波形"):
            arc_json['TimingList'][0]["notes"].append(
                {
                    "type": "arc",
                    "startTime": current_ms,
                    "endTime": current_ms + sr_time,
                    "startPos": startPos,
                    "endPos": endPos,
                    "arcType": "s",
                    "color": color,
                    "hitsound": "none",
                    "IsSkyline": isBlack
                }
            )
        elif setting in ("全波形 0ms", "单侧波形 0ms"):
            arc_json['TimingList'][0]["notes"].append(
                {
                    "type": "arc",
                    "startTime": current_ms,
                    "endTime": current_ms,
                    "startPos": startPos,
                    "endPos": endPos,
                    "arcType": "s",
                    "color": color,
                    "hitsound": "none",
                    "IsSkyline": isBlack
                }
            )

        current_ms += sr_time

    if not isEnforceBased:
        for index, i in enumerate(arc_json['TimingList'][0]["notes"]):
            if index < len(arc_json['TimingList'][0]["notes"])-1:
                i["endPos"] = arc_json['TimingList'][0]["notes"][index+1]["startPos"]

    return arc_json['TimingList'][0]["notes"]


def core(chart_path, music, time_interval, sr_time, setting, isMirrored, isEnforceBased, isXZmode=True, 
         pos_interval=[0,1], flat_pos:int|float = 0, isBlack=True, color="blue", wape:int|float=1):
    
    m_out, sr = process_m(music, time_interval, sr_time)

    if isinstance(m_out, str) and m_out == "ERROR_NO_CHANNEL":
        raise MusicChannelError(f"音频 {music} 存在频道问题")
    
    arc_json = process_c(setting, isMirrored, isEnforceBased, sr, m_out[0], time_interval[0], sr_time, isXZmode, 
                         pos_interval, flat_pos, isBlack, color, wape)

    shutil.copyfile(chart_path, f"{chart_path}.bak")
    print(f"正在处理 {chart_path}")
    chart = jsonaea.load(chart_path)
    chart["TimingList"].append({
                "tags":[],
                "notes":[],
                "timing":[],
                "scenecontrol":[]
            })
    offset = chart["META"]["AudioOffset"]
    for i in arc_json:
        i["startTime"] -= offset
        i["endTime"] -= offset
    
    new_tg = len(chart["TimingList"]) - 1
    chart["TimingList"][new_tg]["notes"].extend(arc_json)

    jsonaea.output(chart, chart_path)

    return len(arc_json)