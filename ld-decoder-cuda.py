#!/usr/bin/python

from __future__ import division
from __future__ import print_function

import numpy as np
import scipy as sp
import scipy.signal as sps
import scipy.fftpack as fftpack 
import matplotlib.pyplot as plt
import sys
import getopt

import fft8 as fft8 
import fdls as fdls
import ld_utils as utils

import pycuda.autoinit
import pycuda.driver as drv
import pycuda.gpuarray as gpuarray
from pycuda.compiler import SourceModule

import skcuda.fft as fft
import skcuda.misc as misc
    
import cProfile

pi = np.pi
tau = np.pi * 2

freq = (315.0 / 88.0) * 8.00
freq_hz = freq * 1000000.0

afreq = freq / 8.0
afreq_hz = afreq * 1000000.0

blocklenk = 64
blocklen = (blocklenk * 1024)  

ablocklenk = blocklenk//4 
ablocklen = (ablocklenk * 1024)  

lowpass_filter_b, lowpass_filter_a = sps.butter(8, (4.5/(freq/2)), 'low')

# stubs for later
f_deemp_b = []
f_deemp_a = []

# These are NTSC defaults.  They may be reprogrammed before starting
SysParams_NTSC = {
	'analog_audio': True, # not true for later PAL
	'audio_lfreq': 2301136,
	'audio_rfreq': 2812499,

	'fsc_mhz': (315.0 / 88.0),

	# video frequencies 
	'videorf_sync': 7600000,
	'videorf_0ire': 8100000,
	'videorf_100ire': 9300000, # slightly higher on later disks

	'ire_min': -60,
	'ire_max': 140,

	# changeable defaults
	'deemp': (.825, 2.35),

	'vbpf': (3200000, 14000000),
	'vlpf_freq': 4.4,	# in mhz
	'vlpf_order': 5		# butterworth filter order
}

SP = SysParams_NTSC

# default deemp constants				
deemp_t1 = .825
deemp_t2 = 2.35

# audio filters
Baudiorf = sps.firwin(65, 3.5 / (freq / 2), window='hamming', pass_zero=True)
Aaudiorf = [1.0]

left_audfreq = 2.301136
right_audfreq = 2.812499

N, Wn = sps.buttord([(left_audfreq-.10) / (freq / 2.0), (left_audfreq+.10) / (freq / 2.0)], [(left_audfreq-.15) / (freq / 2.0), (left_audfreq+.15)/(afreq / 2.0)], 1, 15)
#print(N,Wn)
Baudl, Aaudl = sps.butter(N, Wn, btype='bandpass')

N, Wn = sps.buttord([(right_audfreq-.10) / (freq / 2.0), (right_audfreq+.10) / (freq / 2.0)], [(right_audfreq-.15) / (freq / 2.0), (right_audfreq+.15)/(freq / 2.0)], 1, 15)
Baudr, Aaudr = sps.butter(N, Wn, btype='bandpass')

#Baudl = sps.firwin(129, [2.10 / (freq / 2.0), 2.50 / (freq / 2.0)], pass_zero=False)
#Aaudl = [1.0]

N, Wn = sps.buttord(0.016 / (afreq / 2.0), 0.024 / (afreq / 2.0), 2, 15) 
audiolp_filter_b, audiolp_filter_a = sps.butter(N, Wn)

N, Wn = sps.buttord(3.1 / (freq / 2.0), 3.5 / (freq / 2.0), 1, 20) 
audiorf_filter_b, audiorf_filter_a = sps.butter(N, Wn, btype='lowpass')

# from http://tlfabian.blogspot.com/2013/01/implementing-hilbert-90-degree-shift.html
hilbert_filter = np.fft.fftshift(
    np.fft.ifft([0]+[1]*200+[0]*200)
)
fft_hilbert = np.fft.fft(hilbert_filter, blocklen)

mod = SourceModule("""
#include <cuComplex.h>

__global__ void anglediff(float *out, cuComplex *in) {
	const int i = threadIdx.x  + blockIdx.x * blockDim.x; // + (1024 * threadIdx.y);
	
	float tmp = atan2f(in[i + 1].y, in[i + 1].x) - atan2f(in[i].y, in[i].x);
	
	out[i] = (tmp >= 0) ? tmp : tmp + (3.14159265359 * 2);
}

__global__ void clamp16(unsigned short *y, float *x, float offset, float mul) {
	const int i = threadIdx.x  + blockIdx.x * blockDim.x; // + (1024 * threadIdx.y);

	float tmp = x[i];
	
	tmp += offset;
	tmp *= mul;

	y[i] = (unsigned short)max((float)0, min((float)65535, tmp));
}

__global__ void anglediff_mac(float *out, cuComplex *in, float mult, float add) {
	const int i = threadIdx.x  + blockIdx.x * blockDim.x; // + (1024 * threadIdx.y);
	
	float tmp = atan2f(in[i + 1].y, in[i + 1].x) - atan2f(in[i].y, in[i].x);
	
	tmp = (tmp >= 0) ? tmp : tmp + (3.14159265359 * 2);
	tmp *= mult;
	tmp += add;
	out[i] = max((float)-200000, min((float)200000, tmp));
}

__global__ void audioscale(float *out, float *in_left, float *in_right, float s, float offset)
{
	const int i = (threadIdx.x  + blockIdx.x * blockDim.x);

	int index = (s * i) + offset;

	out[i * 2] = in_left[index];
	out[(i * 2)+ 1] = in_right[index];
}

""")

def FFTtoGPU(fft):
	return gpuarray.to_gpu((np.complex64(fft))[0:len(fft)//2])

minire = -60
maxire = 140

hz_ire_scale = (9300000 - 8100000) / 100
minn = 8100000 + (hz_ire_scale * -60)

out_scale = 65534.0 / (maxire - minire)
	
Bbpf, Abpf = sps.butter(1, [3.2/(freq/2), 14.0/(freq/2)], btype='bandpass')
Bcutl, Acutl = sps.butter(1, [2.25/(freq/2), 2.35/(freq/2)], btype='bandstop')
Bcutr, Acutr = sps.butter(1, [2.75/(freq/2), 2.850/(freq/2)], btype='bandstop')
# AC3 - Bcutr, Acutr = sps.butter(1, [2.68/(freq/2), 3.08/(freq/2)], btype='bandstop')

lowpass_filter_b, lowpass_filter_a = sps.butter(5, (4.4/(freq/2)), 'low')

forder = 256 
forderd = 0 
[Bbpf_FDLS, Abpf_FDLS] = fdls.FDLS_fromfilt(Bbpf, Abpf, forder, forderd, 0, phasemult = 1.00)
[Bcutl_FDLS, Acutl_FDLS] = fdls.FDLS_fromfilt(Bcutl, Acutl, forder, forderd, 0, phasemult = 1.00)
[Bcutr_FDLS, Acutr_FDLS] = fdls.FDLS_fromfilt(Bcutr, Acutr, forder, forderd, 0, phasemult = 1.00)

Fbpf = np.fft.fft(Bbpf_FDLS, blocklen)
Fcutl = np.fft.fft(Bcutl_FDLS, blocklen)
Fcutr = np.fft.fft(Bcutr_FDLS, blocklen)

[Baudrf_FDLS, Aaudrf_FDLS] = fdls.FDLS_fromfilt(audiorf_filter_b, audiorf_filter_a, forder, forderd, 0)
Faudrf = np.fft.fft(Baudrf_FDLS, blocklen)
Faudrf_GPU = FFTtoGPU(Faudrf)

audiolp_filter_fir = sps.firwin(257, .020 / (afreq / 2.0))
[Baudiolp_FDLS, Aaudiolp_FDLS] = fdls.FDLS_fromfilt(audiolp_filter_b, audiolp_filter_a, forder, forderd, 0)
[Baudiolp_FDLS, Aaudiolp_FDLS] = fdls.FDLS_fromfilt(audiolp_filter_fir, [1.0], forder, forderd, 0)
FiltAPost = np.fft.fft(Baudiolp_FDLS, blocklen)
FiltAPost = FiltAPost * FiltAPost * FiltAPost
FiltAPost_GPU = FFTtoGPU(FiltAPost)

[Baudl_FDLS, Aaudl_FDLS] = fdls.FDLS_fromfilt(Baudl, Aaudl, forder, forderd, 0)
[Baudr_FDLS, Aaudr_FDLS] = fdls.FDLS_fromfilt(Baudr, Aaudr, forder, forderd, 0)

Faudl = np.fft.fft(Baudl_FDLS, blocklen)
Faudr = np.fft.fft(Baudr_FDLS, blocklen)

Fhilbert = np.fft.fft(hilbert_filter, blocklen)

FiltV = Fbpf * Fcutl * Fcutr * Fhilbert
FiltAL = Faudrf * Faudl * Fhilbert
FiltAL_GPU = FFTtoGPU(FiltAL)
FiltAR = Faudrf * Faudr * Fhilbert
FiltAR_GPU = FFTtoGPU(FiltAR)

##FiltV[0:7500] = FiltV[0:7500] * 0
#FiltV[32000:32768] = FiltV[32000:32768] * 0
FiltV_GPU = FFTtoGPU(FiltV)

# octave:104> t1 = 100; t2 = 55; [b, a] = bilinear(-t2*(10^-8), -t1*(10^-8), t1/t2, freq); freqz(b, a)
# octave:105> printf("f_emp_b = ["); printf("%.15e, ", b); printf("]\nf_emp_a = ["); printf("%.15e, ", a); printf("]\n")
f_emp_b = [1.293279022403258e+00, -1.018329938900196e-02, ]
f_emp_a = [1.000000000000000e+00, 2.830957230142566e-01, ]

[Bemp_FDLS, Aemp_FDLS] = fdls.FDLS_fromfilt(f_emp_b, f_emp_a, forder, forderd, 0)
Femp = np.fft.fft(Bemp_FDLS, blocklen)

FiltVInner = FiltV * Femp
FiltVInner_GPU = FFTtoGPU(FiltVInner)

FiltPost = []
FiltPost_GPU = []

Inner = 0

# CUDA structures
cs_first = True
cs = {} 

def prepare_video_filters():
	# set up deemp filter
	[tf_b, tf_a] = sps.zpk2tf(-SP['deemp'][1]*(10**-8), -SP['deemp'][0]*(10**-8), SP['deemp'][0] / SP['deemp'][1])
	SP['f_deemp'] = sps.bilinear(tf_b, tf_a, 1/(freq_hz/2))

	# TODO:  test these CLV+innerCAV parameters.  Should be same on PAL+NTSC 
	t1 = 25
	t2 = 13.75
	
	[tf_b, tf_a] = sps.zpk2tf(-t2*(10**-8), -t1*(10**-8), t1 / t2)
	SP['f_emp'] = sps.bilinear(tf_b, tf_a, 1/(freq_hz/2))

	SP['f_videorf_bpf'] = sps.butter(1, [SP['vbpf'][0]/(freq_hz/2), SP['vbpf'][1]/(freq_hz/2)], btype='bandpass')

	if SP['analog_audio'] == True:
		SP['f_aleft_stop'] = sps.butter(1, [(SP['audio_lfreq'] - 750000)/(freq_hz/2), (SP['audio_lfreq'] + 750000)/(freq_hz/2)], btype='bandstop')
		SP['f_aright_stop'] = sps.butter(1, [(SP['audio_rfreq'] - 750000)/(freq_hz/2), (SP['audio_rfreq'] + 750000)/(freq_hz/2)], btype='bandstop')

	lowpass_filter_b, lowpass_filter_a = sps.butter(SP['vlpf_order'], SP['vlpf_freq']/(freq/2), 'low')

	# if AC3:
	#SP['f_arightcut'] = sps.butter(1, [(2650000)/(freq_hz/2), (3150000)/(freq_hz/2)], btype='bandstop')

	# prepare for FFT: convert above filters to FIR using LPF 
	forder = 256 
	forderd = 0 

	[Bbpf_FDLS, Abpf_FDLS] = fdls.FDLS_fromfilt(SP['f_videorf_bpf'][0], SP['f_videorf_bpf'][1], forder, forderd, 0, phasemult = 1.00)
	[Bemp_FDLS, Aemp_FDLS] = fdls.FDLS_fromfilt(SP['f_emp'][0], SP['f_emp'][1], forder, forderd, 0)
	[Bdeemp_FDLS, Adeemp_FDLS] = fdls.FDLS_fromfilt(SP['f_deemp'][0], SP['f_deemp'][1], forder, forderd, 0)
	[Bplpf_FDLS, Aplpf_FDLS] = fdls.FDLS_fromfilt(lowpass_filter_b, lowpass_filter_a, forder, forderd, 0)
	if SP['analog_audio'] == True:
		[Bcutl_FDLS, Acutl_FDLS] = fdls.FDLS_fromfilt(SP['f_aleft_stop'][0], SP['f_aleft_stop'][1], forder, forderd, 0, phasemult = 1.00)
		[Bcutr_FDLS, Acutr_FDLS] = fdls.FDLS_fromfilt(SP['f_aright_stop'][0], SP['f_aright_stop'][1], forder, forderd, 0, phasemult = 1.00)
		Fcutl = np.fft.fft(Bcutl_FDLS, blocklen)
		Fcutr = np.fft.fft(Bcutr_FDLS, blocklen)

	Fbpf = np.fft.fft(Bbpf_FDLS, blocklen)
	Femp = np.fft.fft(Bemp_FDLS, blocklen)

	SP['fft_video'] = Fbpf * Fhilbert

	if SP['analog_audio'] == True:
		[Bcutl_FDLS, Acutl_FDLS] = fdls.FDLS_fromfilt(SP['f_aleft_stop'][0], SP['f_aleft_stop'][1], forder, forderd, 0, phasemult = 1.00)
		[Bcutr_FDLS, Acutr_FDLS] = fdls.FDLS_fromfilt(SP['f_aright_stop'][0], SP['f_aright_stop'][1], forder, forderd, 0, phasemult = 1.00)
		Fcutl = np.fft.fft(Bcutl_FDLS, blocklen)
		Fcutr = np.fft.fft(Bcutr_FDLS, blocklen)
		SP['fft_video'] *= (Fcutl * Fcutr)
	
	SP['fft_video_inner'] = SP['fft_video'] * Femp

	# Post processing:  lowpass filter + deemp
	Fplpf = np.fft.fft(Bplpf_FDLS, blocklen)
	Fdeemp = np.fft.fft(Bdeemp_FDLS, blocklen)
	SP['fft_post'] = Fplpf * Fdeemp	

def prepare_video_cuda():

	# Things go *a lot* faster when you have the memory structures pre-allocated
	cs['plan1'] = fft.Plan(blocklen, np.float32, np.complex64)
	cs['plan1i'] = fft.Plan(blocklen, np.complex64, np.complex64)
	
	cs['fft1_out'] = gpuarray.empty((blocklen//2)+1, np.complex64)

	cs['filtered1'] = gpuarray.empty(blocklen, np.complex64)
	cs['fm_demod'] = gpuarray.empty(blocklen, np.float32)

	cs['postlpf'] = gpuarray.empty(blocklen, np.float32)

	cs['fft2_out'] = gpuarray.empty((blocklen//2)+1, np.complex64)
	
	cs['clipped_gpu'] = gpuarray.empty(blocklen, np.uint16)
	
	cs['plan2'] = fft.Plan(blocklen, np.float32, np.complex64)
	cs['plan2i'] = fft.Plan(blocklen, np.complex64, np.float32)
	
	cs['doclamp16'] = mod.get_function("clamp16")
	cs['doanglediff'] = mod.get_function("anglediff")

	cs['filt_post'] = FFTtoGPU(SP['fft_post'])
	cs['filt_video'] = FFTtoGPU(SP['fft_video'])
	cs['filt_video_inner'] = FFTtoGPU(SP['fft_video_inner'])

def process_video_cuda(data):
	global cs, cs_first 
#	fft_overlap(data, FiltV_GPU) 

	if cs_first == True:
		prepare_video_filters()
		prepare_video_cuda()
		cs_first = False


	fdata = np.float32(data)

	gpudata = gpuarray.to_gpu(fdata)

	# first fft->ifft cycle applies pre-decoding filtering (low pass filters, CAV/CLV emphasis)
	# and very importantly, performs the Hilbert transform
	fft.fft(gpudata, cs['fft1_out'], cs['plan1'])

	if Inner:	
		cs['fft1_out'] *= cs['filt_video_inner']
	else:
		cs['fft1_out'] *= cs['filt_video']
	
	fft.ifft(cs['fft1_out'], cs['filtered1'], cs['plan1i'], True)

	cs['doanglediff'](cs['fm_demod'], cs['filtered1'], block=(1024,1,1), grid=(blocklenk,1))

	# post-processing:  output low-pass filtering and deemphasis	
	fft.fft(cs['fm_demod'], cs['fft2_out'], cs['plan2'])
	cs['fft2_out'] *= cs['filt_post'] 
	fft.ifft(cs['fft2_out'], cs['postlpf'], cs['plan2i'], True)

	sminn = minn / (freq_hz / tau)
	mult = (freq_hz / tau) * (out_scale / hz_ire_scale)

	cs['doclamp16'](cs['clipped_gpu'], cs['postlpf'], np.float32(-sminn), np.float32(mult), block=(1024,1,1), grid=(blocklenk,1)) 

	output_16 = cs['clipped_gpu'].get()

	chop = 512
	return output_16[chop:len(output_16)-chop]

# graph for debug
#	output = (sps.lfilter(f_deemp_b, f_deemp_a, output)[128:len(output)]) / deemp_corr

#	plt.plot(cs['postlpf'].get()[5000:7500])
	plt.plot(output_16[5000:7000])
#	plt.plot(range(0, len(output_16)), output_16)
#	plt.plot(range(0, len(doutput)), doutput)
#	plt.plot(range(0, len(output_prefilt)), output_prefilt)
	plt.show()
	exit()

left_audfreqm = left_audfreq * 1000000
right_audfreqm = right_audfreq * 1000000

csa_first = True
csa = {} 
	
def prepare_audio_cuda(data):
	fdata = np.float32(data)
	gpudata = gpuarray.to_gpu(fdata)

	cs['plan1'] = fft.Plan(gpudata.shape, np.float32, np.complex64)
	cs['plan1i'] = fft.Plan(ablocklen, np.complex64, np.complex64)
	
	cs['fft1_out'] = gpuarray.empty((len(fdata)//2)+1, np.complex64)
	cs['ifft1_out'] = gpuarray.empty(len(fdata), np.complex64)
	
	cs['fm_left'] = gpuarray.empty(ablocklen, np.complex64)
	cs['fm_right'] = gpuarray.empty(ablocklen, np.complex64)
	
	cs['left_clipped'] = gpuarray.empty(ablocklen, np.float32)
	cs['right_clipped'] = gpuarray.empty(ablocklen, np.float32)
	
	cs['left_fft1'] = gpuarray.empty(blocklen//2+1, np.complex64)
	cs['right_fft1'] = gpuarray.empty(blocklen//2+1, np.complex64)
	cs['left_fft2'] = gpuarray.empty(ablocklen//2+1, np.complex64)
	cs['right_fft2'] = gpuarray.empty(ablocklen//2+1, np.complex64)

	cs['left_out'] = gpuarray.empty(ablocklen, np.float32)
	cs['right_out'] = gpuarray.empty(ablocklen, np.float32)
	
	cs['plan2'] = fft.Plan(ablocklen, np.float32, np.complex64)
	cs['plan2i'] = fft.Plan(ablocklen, np.complex64, np.float32)
	
	cs['outlen'] = outlen = (((ablocklen - 1536) // 20) // 32) * 32
	cs['scaledout'] = gpuarray.empty(outlen * 2, np.float32)
	cs['left_scaledout'] = gpuarray.empty(outlen, np.float32)
	cs['right_scaledout'] = gpuarray.empty(outlen, np.float32)
	
	cs['doanglediff_mac'] = mod.get_function("anglediff_mac")
	cs['doaudioscale'] = mod.get_function("audioscale")

def process_audio_cuda(data):
	global cs, csa, csa_first
	
	if csa_first == True:
		prepare_audio_cuda(data)
		csa_first = False	
	
	fdata = np.float32(data)
	gpudata = gpuarray.to_gpu(fdata)
	
	fft.fft(gpudata, cs['fft1_out'], cs['plan1'])

	cs['left_fft1'] = (cs['fft1_out'] * FiltAL_GPU) # [0:blocklen])[0:(ablocklen//2)+1]
	cs['right_fft1'] = (cs['fft1_out'] * FiltAR_GPU)[0:(ablocklen//2)+1]

	fft.ifft(cs['left_fft1'][0:(ablocklen//2)+1], cs['fm_left'], cs['plan1i'], True)
	fft.ifft(cs['right_fft1'], cs['fm_right'], cs['plan1i'], True)

	#plt.plot(fdata[4000:5000])
#	plt.plot(np.absolute(cs['fm_left'].get())[4000:5000])

	cs['doanglediff_mac'](cs['left_clipped'], cs['fm_left'], np.float32((afreq_hz / 1.0 / np.pi)), np.float32(-left_audfreqm), block=(1024,1,1), grid=(ablocklenk,1))
	cs['doanglediff_mac'](cs['right_clipped'], cs['fm_right'], np.float32((afreq_hz / 1.0 / np.pi)), np.float32(-right_audfreqm), block=(1024,1,1), grid=(ablocklenk,1))

	fft.fft(cs['left_clipped'], cs['left_fft2'], cs['plan2'])
	fft.fft(cs['right_clipped'], cs['right_fft2'], cs['plan2'])
	
	cs['left_fft2'] *= FiltAPost_GPU
	cs['right_fft2'] *= FiltAPost_GPU
	
	fft.ifft(cs['left_fft2'], cs['left_out'], cs['plan2i'], True)
	fft.ifft(cs['right_fft2'], cs['right_out'], cs['plan2i'], True)

	aclip = 256 

	outlen = (ablocklen - (aclip * 2)) // 20
	cs['doaudioscale'](cs['scaledout'], cs['left_out'], cs['right_out'], np.float32(20), np.float32(aclip), block=(32, 1, 1), grid=(outlen//32,1));

	return cs['scaledout'].get(), outlen * 80

	plt.plot(cs['scaledout'].get())

#	plt.plot(cs['left_out'].get()[768:-768])
#	plt.plot(cs['right_out'].get()[768:-768] + 100000)
	plt.show()
	exit()

def test():
	test = np.empty(blocklen, dtype=np.int16)

#	infile = open("noise.raw", "rb")

#	noisebuf = infile.read(blocklen)
#	noisedata = (np.fromstring(noisebuf, 'uint8', blocklen)) 
#	noisedata = np.float(noisedata)

#	for hlen in range(3, 18):
	for vlevel in range(64, 101, 5):

		vphase = 0
		alphase = 0
		arphase = 0

		for i in range(0, len(test)):
			if i > len(test) / 2:
				vfreq = 9300000
			else:
				vfreq = 8100000

			vphase += vfreq / freq_hz  
			alphase += 2300000 / freq_hz 
			arphase += 2800000 / freq_hz 
			tmp = (np.sin(vphase * tau) * vlevel)
			tmp += (np.sin(alphase * tau) * vlevel / 10.0)
			tmp += (np.sin(arphase * tau) * vlevel / 10.0)
#			tmp += noisedata[i] * 1
			test[i] = tmp + 32768 

		output = np.float(process_video_cuda(test)[(blocklen/2)+1000:(blocklen/2)+5096])
		plt.plot(range(0, len(output)), output)

		output /= out_scale
		output -= 60

		mean = np.mean(output)
		std = np.std(output)
		print(vlevel, mean, std, 20 * np.log10(mean / std)) 

#	plt.show()
	exit()

def main():
	global lowpass_filter_b, lowpass_filter_a 
	global hz_ire_scale, minn
	global f_deemp_b, f_deemp_a

	global SP, FiltPost, FiltPost_GPU

	global Bcutr, Acutr
	
	global Inner 

	global blocklen, blocklenk

	outfile = sys.stdout #.buffer
	audio_mode = 0 
	CAV = 0

	byte_start = 0
	byte_end = 0

	f_seconds = False 

	optlist, cut_argv = getopt.getopt(sys.argv[1:], "d:D:hLCaAwSs:")

	for o, a in optlist:
		if o == "-d":
			SP['deemp'][0] = np.double(a)
		if o == "-D":
			SP['deemp'][1] = np.double(a)
		if o == "-a":
			audio_mode = 1	
#			blocklen = blocklen * 2 
#			blocklenk = blocklenk * 2 
		if o == "-L":
			Inner = 1
		if o == "-A":
			CAV = 1
			Inner = 1
#		if o == "-h":
			# use full spec deemphasis filter - will result in overshoot, but higher freq resonse
#			f_deemp_b = [3.778720395899611e-01, -2.442559208200777e-01]
#			f_deemp_a = [1.000000000000000e+00, -8.663838812301168e-01]
		if o == "-C":
			Bcutr, Acutr = sps.butter(1, [2.50/(freq/2), 3.26/(freq/2)], btype='bandstop')
			Bcutr, Acutr = sps.butter(1, [2.68/(freq/2), 3.08/(freq/2)], btype='bandstop')
		if o == "-w":
			hz_ire_scale = (9360000 - 8100000) / 100
			minn = 8100000 + (hz_ire_scale * -60)
		if o == "-S":
			f_seconds = True
		if o == "-s":
			ia = int(a)
			if ia == 0:
				lowpass_filter_b, lowpass_filter_a = sps.butter(5, (4.2/(freq/2)), 'low')
			if ia == 1:	
				lowpass_filter_b, lowpass_filter_a = sps.butter(5, (4.4/(freq/2)), 'low')
			if ia == 2:	
				lowpass_filter_b, lowpass_filter_a = sps.butter(6, (4.6/(freq/2)), 'low')
				lowpass_filter_b, lowpass_filter_a = sps.butter(6, (4.6/(freq/2)), 'low')
				SP['deemp_t1'] = .825
				SP['deemp_t2'] = 2.35
			if ia == 3:	
				# high frequency response - and ringing.  choose your poison ;)	
				lowpass_filter_b, lowpass_filter_a = sps.butter(10, (5.0/(freq/2)), 'low')
				lowpass_filter_b, lowpass_filter_a = sps.butter(7, (5.0/(freq/2)), 'low')
			if ia == 4:	
				lowpass_filter_b, lowpass_filter_a = sps.butter(10, (5.3/(freq/2)), 'low')
				lowpass_filter_b, lowpass_filter_a = sps.butter(7, (5.3/(freq/2)), 'low')
			if ia == 51:	
				lowpass_filter_b, lowpass_filter_a = sps.butter(5, (4.4/(freq/2)), 'low')
			if ia == 61:	
				lowpass_filter_b, lowpass_filter_a = sps.butter(6, (4.4/(freq/2)), 'low')
			if ia == 62:	
				lowpass_filter_b, lowpass_filter_a = sps.butter(6, (4.7/(freq/2)), 'low')

	# set up deemp filter

	#test()

	argc = len(cut_argv)
	if argc >= 1:
		infile = open(cut_argv[0], "rb")
	else:
		infile = sys.stdin

	byte_start = 0
	if (argc >= 2):
		byte_start = float(cut_argv[1])

	if (argc >= 3):
		byte_end = float(cut_argv[2])
		limit = 1
	else:
		limit = 0

	if f_seconds:
		byte_start *= freq_hz 
		byte_end *= freq_hz 
	else:
		byte_end += byte_start

	byte_end -= byte_start

	byte_start = int(byte_start)
	byte_end = int(byte_end)

	if (byte_start > 0):	
		infile.seek(byte_start)
	
	if CAV and byte_start > 11454654400:
		CAV = 0
		Inner = 0 

	total = toread = blocklen 
	inbuf = infile.read(toread)
	indata = np.fromstring(inbuf, 'uint8', toread)
	
	total = 0
	total_prevread = 0
	total_read = 0

	while (len(inbuf) > 0):
		toread = blocklen - indata.size 

		if toread > 0:
			inbuf = infile.read(toread)
			indata = np.append(indata, np.fromstring(inbuf, 'uint8', len(inbuf)))

			if indata.size < blocklen:
				pycuda.driver.stop_profiler()
				exit()

		if audio_mode:	
			output, osamp = process_audio_cuda(indata)
			
			nread = osamp 
			outfile.write(output)
		else:
			output_16 = process_video_cuda(indata)
			outfile.write(output_16)
			nread = len(output_16)
			
			total_read += nread

			if CAV:
				if (total_read + byte_start) > 11454654400:
					CAV = 0
					Inner = 0

		indata = indata[nread:len(indata)]

		if limit == 1:
			byte_end -= toread 
			if (byte_end < 0):
				inbuf = ""

	pycuda.driver.stop_profiler()


if __name__ == "__main__":
#	cProfile.run('main()')
	main()

