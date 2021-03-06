#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Run MATCH MOI Reporter on a VCF file and generate a CSV with result that can 
# be imported into Excel for a collated report
#
# 2/11/2014
################################################################################
"""
Collect MOI reports for a set of VCF files and output a raw CSV formatted output 
that can be easily imported into Microsoft Excel for reporting. This script
relies on `match_moi_report.pl` in order to generate the MOI reports for each.
"""
import sys
import os
import re
import subprocess
import argparse
import multiprocessing

from termcolor import colored
from natsort import natsorted
from collections import defaultdict
from pprint import pprint as pp # noqa
from multiprocessing.pool import ThreadPool # noqa

version = '4.2.061019'
debug = False


def get_args():
    # Default thresholds. Put them here rather than fishing below.
    num_procs = multiprocessing.cpu_count() - 1
    cn = 7
    cu = None
    cl = None
    reads = 1000

    parser = argparse.ArgumentParser(description = __doc__)
    parser.add_argument(
        'vcf_files',
        metavar="<vcf_files>",
        nargs="+",
        help="List of VCF files to process."
    )
    parser.add_argument(
        '--cn', 
        metavar='INT', 
        default = cn,
        type = int,
        help='Use copy number (CN) value for CNV reporting to be more compatible '
            'with MATCH rules. This will disable CU and CL thresholds and is on '
            'by default. %s' % colored("DEFAULT: CN=%(default)s", 'green')
    )
    parser.add_argument(
        '--cu', 
        default=cu, 
        metavar='INT', 
        type = int,
        help='Copy number threshold ({} CI lower bound) to pass to '
            'match_moi_report for reporting amplifications. {}'.format(
                '5%%', colored('DEFAULT: 5%% CI=%(default)s', 'green'))
    )
    parser.add_argument(
        '--cl', 
        default=cl, 
        metavar='INT',
        type = int,
        help='Copy number threshold ({} CI upper bound) to pass to '
            'match_moi_report for reporting copy loss. {}'.format(
                '95%%',  colored('DEFAULT: 95%% CI=%(default)s', 'green'))
    )
    parser.add_argument(
        '--reads', 
        default=reads, 
        metavar='INT', 
        type = int,
        help='Threshold for number of fusion reads to report. %s' % 
        colored('DEFAULT: Reads=%(default)s', 'green')
    )
    parser.add_argument(
        '-p', '--pedmatch', 
        action='store_true', 
        help='Data comes from Pediatric MATCH rather than Adult MATCH.'
    )
    parser.add_argument(
        '-b', '--blood', 
        action='store_true', 
        help='Data comes from blood specimens, and therefore we only have DNA '
            'data.'
    )
    parser.add_argument(
        '-n', '--num_procs',
        metavar="INT <num_procs>",
        type=int,
        default=num_procs,
        help='Number of thread pools to use. {}'.format(
            colored('DEFAULT: %(default)s procs', 'green'))
    )
    parser.add_argument(
        '-o','--output', 
        metavar="<output file>",
        help='Output to file rather than STDOUT.'
    )
    parser.add_argument(
        '-q','--quiet', 
        action='store_false', 
        default=True, 
        help='Do not suppress warning and extra output'
    )
    parser.add_argument(
        '-v', '--version', 
        action='version',
        version = '%(prog)s - ' + version
    )
    args = parser.parse_args()

    if any(x for x in (args.cu, args.cl)):
        args.cn = None
        if not all(x for x in (args.cu, args.cl)):
            sys.stderr.write("ERROR: you must indicate both a 5% *and* 95% CI "
                "value when using the --cu and --cl option.\n")
            sys.exit(1)

    global quiet
    quiet = args.quiet
    return args

def get_names(string):
    string = os.path.basename(string)
    try:
        (dna_samp,rna_samp) = re.search('(.*?DNA)_(.*).vcf$',string).group(1,2)
    except:
        if not quiet:
            sys.stderr.write("WARN: Can not get DNA or RNA sample name for '%s'! "
                "Using full VCF filename instead\n" % string)
        dna_samp = rna_samp = string.rstrip('.vcf')
    return dna_samp, rna_samp

def parse_cnv_params(cu, cl, cn):
    '''
    Since CNV params are a bit difficult to work with, create a better, 
    standardized list to pass into functions below
    '''
    params_list = []
    params = {
        '--cu' : cu,
        '--cl' : cl,
        '--cn' : cn 
    }
    params_list = [[k, str(v)] for k, v in params.items() if v]
    return sum(params_list, [])

def populate_list(var_type, var_data):
    wanted_fields = {
        'snv'     : [0,9,1,2,3,10,11,12,8,4,5,6,7,13],
        'cnv'     : [0,1,2,5],
        'fusions' : [0,4,2,1,3]
    }
    return [var_data[x] for x in wanted_fields[var_type]]

def pad_list(data_list, data_type):
    '''
    Pad out the list with hyphens where there is no relevent data.  Maybe kludgy, 
    but I don't know a better way
    '''
    tmp_list = ['-'] * 15
    data_list.reverse()
    if data_type == 'cnv':
        for i in [0,1,2,9]:
            tmp_list[i] = data_list.pop()
    elif data_type == 'fusions':
        # Get rid of 
        for i in [0,1,8,4,10]:
            tmp_list[i] = data_list.pop()
    return tmp_list

def get_location(pos, vcf):
    """
    For the cases where we need location information, like Protein Painter,
    re-read the VCF in vcfExtractor, and get the location for the output.
    """
    cmd = ['vcfExtractor.pl', '-N', '-n', '-a', '-p', pos, vcf]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
        encoding='utf8')
    result, error = p.communicate()

    if p.returncode != 0:
        sys.stderr.write("ERROR: Can not process file: {}!\n".format(vcf))

    lines = result.split('\n')
    for l in lines:
        if l.startswith('chr'):
            return l.split()[12] # Field 12 is "Location" field in vcfExtractor

def parse_data(report_data, dna, rna, vcf):
    data = defaultdict(dict)
    raw_data = report_data.split('\n')

    for line in raw_data:
        fields = line.split(',')
        if fields[0] == 'SNV':
            varid = fields[9] +':'+ fields[1]
            data['snv_data'][varid] = [dna] + populate_list('snv', fields)

            # For protein painter kind of output, need the location
            data['snv_data'][varid].append(get_location(fields[1], vcf))

        elif fields[0] == 'CNV':
            varid = fields[1] +':'+ fields[2]
            cnv_data = populate_list('cnv', fields)
            padded_list = pad_list(cnv_data,'cnv')
            data['cnv_data'][varid] = [dna] + padded_list

        elif fields[0] == 'Fusion':
            varid = fields[1] +':'+ fields[2]
            fusion_data = populate_list('fusions', fields)
            padded_list = pad_list(fusion_data,'fusions')
            data['fusion_data'][varid] = [rna] + padded_list

    # Let's still output something even if no MOIs were detected
    if not data:
        data['null']['no_result'] = [dna] + ['-']*11
    return dict(data)

def print_data(var_type,data,outfile):
    # Split the key by a colon and sort based on chr and then pos using the 
    # natsort library
    if var_type == 'null':
        outfile.write(','.join(data['no_result']) + "\n")
    elif var_type == 'snv_data':
        for variant in natsorted(
                data.keys(), key=lambda k: (k.split(':')[1], k.split(':')[2])):
            outfile.write(','.join(data[variant]) + "\n")
    else:
        for variant in natsorted(data.keys(), key=lambda k: k.split(':')[1]):
            outfile.write(','.join(data[variant]) + "\n")
    return

def print_title(fh, cu, cl, cn, reads, pedmatch):
    '''Print out a header to remind me just what params I used this time!'''
    cnv_params = parse_cnv_params(cu, cl, cn)
    #string_params = '='.join([str(x).lstrip('--') for x in cnv_params])
    string_params = '='.join([x.lstrip('--') for x in cnv_params])
    if 'cl' in string_params:
        string_params = string_params.replace('=cl','; cl')
    if pedmatch:
        study_name = 'Pediatric MATCH'
    else:
        study_name = 'Adult MATCH'

    fh.write('-'*95)
    fh.write('\nCollated {} MOI Reports Using Params CNV: {}, Fusion Reads: '
        'reads={}\n'.format(study_name, string_params, reads))
    fh.write('-'*95)
    fh.write('\n')
    return

def gen_moi_report(vcf, params, proc_type):
    '''
    Use MATCH MOI Reporter to generate a variant table we can parse later. Gen 
    CLI Opts to determine what params to run match_moi_report with
    '''
    (dna, rna) = get_names(vcf)
    moi_report_cmd = ['match_moi_report.pl'] + params + [vcf]

    p = subprocess.Popen(moi_report_cmd, stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE, encoding='utf8')
    result, error = p.communicate()

    if p.returncode != 0:
        sys.stderr.write("ERROR: Can not process file: {}!\n".format(vcf))
        raise Exception(error)
    else:
        # need a tuple to track threads and not crash dict entries if we're 
        # doing multithreaded processing.
        if proc_type == 'single':
            return parse_data(result, dna, rna, vcf)
        elif proc_type == 'threaded':
            return vcf, parse_data(result, dna, rna, vcf)

def arg_star(args):
    return gen_moi_report(*args)

def proc_vcfs(vcf_files, params, num_procs):
    '''
    Process the input VCF files using the thresholds set in `params`. Will
    either fork to a parallel process (if num_procs > 1) or process in a single
    loop.
    '''
    moi_data = defaultdict(dict)

    if num_procs < 2:
        sys.stderr.write("Non-parallel processing files (total: %s VCF(s))\n" %
                str(len(vcf_files)))
        for v in vcf_files:
            moi_data[v] = gen_moi_report(v, params, 'single')
    else:
        sys.stderr.write("Parallel processing files using %s processes (total: "
            "%s VCF(s))\n" % (num_procs, str(len(vcf_files))))
        task_list = [(v, params, 'threaded') for v in vcf_files]
        
        pool = ThreadPool(num_procs)
        try:
            moi_data = {vcf : data for vcf, data in pool.imap_unordered(
                arg_star, task_list)}
        except Exception:
            pool.close()
            pool.join()
            raise
        except KeyboardInterrupt:
            pool.close()
            pool.join()
            sys.exit(9)

        # TODO: Not sure if this is needed or not....seem to have a memory leak!
        pool.close()
        pool.join()
    return moi_data

def main(vcfs, cn, cu, cl, reads, pedmatch, blood, output, num_procs, quiet):
    # Setup an output file if we want one
    outfile = ''
    if output:
        print("Writing output to '%s'" % output)
        outfile = open(output, 'w')
    else:
        outfile = sys.stdout

    # Setup MOI Reporter args; start with CNV pipeline args
    moi_reporter_args = parse_cnv_params(cu, cl, cn)

    # Determine if blood or tumor and add appropriate thresholds
    if not blood:
        moi_reporter_args += ['-r', str(reads), '-R']
    else:
        moi_reporter_args.extend(['-R', '-b'])

    # If Pediatric MATCH need to pass different MOI rules.
    if pedmatch:
        moi_reporter_args.append('-p')

    moi_data = proc_vcfs(vcfs, moi_reporter_args, num_procs)

    # Print data
    print_title(outfile, cu, cl, cn, reads, pedmatch)
    sys.exit()

    header = ['Sample', 'Type', 'Gene', 'Position', 'Ref', 'Alt', 
        'Transcript', 'CDS', 'AA', 'VARID', 'VAF/CN', 'Coverage/Counts',
        'RefCov', 'AltCov', 'Function', 'Location']
    outfile.write(','.join(header) + "\n")
    
    # Print out sample data by VCF
    var_types = ['snv_data', 'cnv_data', 'fusion_data', 'null']
    for sample in sorted(moi_data):
        for var_type in var_types:
            try:
                print_data(var_type, moi_data[sample][var_type], outfile)
            except KeyError:
                continue

if __name__ == '__main__':
    args = get_args()
    if debug:
        print('CLI args as passed:')
        pp(vars(args))
        print('')
    main(args.vcf_files, args.cn, args.cu, args.cl, args.reads, args.pedmatch, 
            args.blood, args.output, args.num_procs, args.quiet)
