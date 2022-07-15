from pynta.tasks import *
from pynta.io import IO
from pynta.adsorbates import Adsorbates
import ase.build
from ase.io import read, write
import os
import time
from fireworks import LaunchPad
from fireworks.queue.queue_launcher import rapidfire as rapidfirequeue
from fireworks.utilities.fw_serializers import load_object_from_file
from fireworks.core.rocket_launcher import rapidfire
from fireworks.core.fworker import FWorker
import fireworks.fw_config

class Pynta:
    def __init__(self,path,launchpad_path,fworker_path,rxns_file,surface_type,metal,a=3.6,vaccum=8.0,
    repeats=[(3,3,1),(1,1,4)],slab_path=None,software="Espresso",socket=False,queue=False,
    software_kwargs={'kpts': (3, 3, 1), 'tprnfor': True, 'occupations': 'smearing',
                            'smearing':  'marzari-vanderbilt',
                            'degauss': 0.01, 'ecutwfc': 40, 'nosym': True,
                            'conv_thr': 1e-11, 'mixing_mode': 'local-TF',
                            "pseudopotentials": {"Cu": 'Cu.pbe-spn-kjpaw_psl.1.0.0.UPF',"H": 'H.pbe-kjpaw_psl.1.0.0.UPF',"O": 'O.pbe-n-kjpaw_psl.1.0.0.UPF',"C": 'C.pbe-n-kjpaw_psl.1.0.0.UPF',"N": 'N.pbe-n-kjpaw_psl.1.0.0.UPF',
                            }},
         queue_adapter_path=None):
        self.surface_type = surface_type
        launchpad = LaunchPad.from_file(launchpad_path)
        launchpad.reset('', require_password=False)
        self.launchpad = launchpad
        self.slab_path = slab_path
        self.vaccum = vaccum
        self.a = a
        self.software = software
        self.socket = socket
        self.repeats = repeats
        self.path = os.getcwd() if path is None else path
        self.facet = metal + surface_type
        self.fws = []
        self.rxns = IO.open_yaml_file(rxns_file)
        self.metal = metal
        self.adsorbate_fw_dict = dict()
        self.software_kwargs = software_kwargs
        self.queue = queue
        self.fworker = None
        self.qadapter = None
        self.fworker = FWorker.from_file(fworker_path)
        self.rxns_file = rxns_file
        self.slab = read(self.slab_path) if self.slab_path else None
        if queue:
            self.qadapter = load_object_from_file(queue_adapter_path)

    def generate_slab(self):
        slab_type = getattr(ase.build,self.surface_type)
        slab = slab_type(self.metal,self.repeats[1],self.a,self.vaccum)
        slab.pbc = (True, True, False)
        write(os.path.join(self.path,"slab_init.xyz"),slab)
        self.slab_path = os.path.join(self.path,"slab.xyz")
        fwslab = optimize_firework(os.path.join(self.path,"slab_init.xyz"),self.software,"slab",
            opt_method="BFGSLineSearch",socket=self.socket,software_kwargs=self.software_kwargs,
            run_kwargs={"fmax" : 0.01},out_path=os.path.join(self.path,"slab.xyz"))
        return fwslab

    def setup_adsorbates(self):
        put_adsorbates = Adsorbates(self.path, self.slab_path,
                    self.repeats[0], self.rxns_file, self.path)
        nslab = len(put_adsorbates.slab_atom)
        adsorbate_dict = put_adsorbates.adjacency_to_3d()
        slab = read(self.slab_path)
        big_slab = small_slab * self.repeats[0]
        for adsname,adsorbate in adsorbate_dict.items():
            xyzs = []
            optfws = []
            for prefix,structure in adsorbate.items():
                big_slab_ads = big_slab + structure[nslab:]
                os.makedirs(os.path.join(self.path,"Adsorbates",adsname,prefix))
                write(os.path.join(self.path,"Adsorbates",adsname,prefix,prefix+"_init.xyz"),big_slab_ads)
                xyz = os.path.join(self.path,"Adsorbates",adsname,prefix,prefix+".xyz")
                xyzs.append(xyz)
                fwopt = optimize_firework(os.path.join(self.path,"Adsorbates",adsname,prefix,prefix+"_init.xyz"),
                    self.software,prefix,
                    opt_method="QuasiNewton",socket=self.socket,software_kwargs=self.software_kwargs,
                    run_kwargs={"fmax" : 0.01, "steps" : 70},parents=ads_parents,constraints=["freeze slab"])

                fwvib = vibrations_firework(os.path.join(self.path,"Adsorbates",adsname,prefix,prefix+".xyz"),self.software,
                    prefix,software_kwargs=self.software_kwargs,parents=ads_parents+[fwopt],constraints=["freeze slab"])
                optfws.append(fwopt)

            vib_obj_dict = {"software": self.software, "label": prefix, "software_kwargs": self.software_kwargs,
                "constraints": ["freeze slab"]}
            ctask = MolecularCollect(xyzs,False,[vibrations_firework], [vib_obj_dict],
                    ["vib.json"],[False])
            cfw = Firework([ctask],parents=optfws)
            self.adsorbate_fw_dict[adsname] = cfw
            self.fws.extend(optfws+[cfw])


    def setup_transition_states(self):
        opt_obj_dict = {"software":self.software,"label":"prefix","socket":self.socket,"software_kwargs":self.software_kwargs,
                "run_kwargs": {"fmax" : 0.01, "steps" : 70},"constraints": ["freeze slab"],"sella":True,"order":1}
        vib_obj_dict = {"software":self.software,"label":"prefix","socket":self.socket,"software_kwargs":self.software_kwargs,
                "constraints": constraints}
        TSnudge_obj_dict = {"software":self.software,"label":"prefix","socket":self.socket,"software_kwargs":self.software_kwargs,
                "run_kwargs":{"fmax" : 0.01, "steps" : 70},"constraints":["freeze slab"],"opt_method":"QuasiNewton"}
        for i,rxn in enumerate(self.rxns):
            ts_path = os.path.join(self.path,"TS"+str(i))
            os.makedirs(ts_path)

            ts_task = MolecularTSEstimate(rxn,ts_path,self.slab_path,os.path.join(self.path,"Adsorbates"),
                self.rxns_file,self.repeats[0],self.path.self.metal,out_path=ts_path,scfactor=1.4,scfactor_surface=1.0,
                    scaled1=True,scaled2=False,spawn_jobs=True,opt_obj_dict=opt_obj_dict,vib_obj_dict=vib_obj_dict,
                    TSnudge_obj_dict=TSnudge_obj_dict)
            reactants,products = IO.get_reactants_and_products(rxn)
            parents = []
            for m in reactants+products:
                parents.append(self.adsorbate_fw_dict[m])
            fw = Firework([ts_task],parents=parents)
            self.fws.append(fw)

    def rapidfire(self):
        if self.queue:
            rapidfirequeue(self.launchpad,self.fworker,self.qadapter)
        else:
            rapidfire(self.launchpad,fworker=self.fworker)

    def execute(self):
        if self.slab_path is None: #handle slab
            fwslab = self.generate_slab()
            wfslab = Workflow([fwslab], name="slab")
            self.launchpad.add_wf(wfslab)
            self.rapidfire()
            while not os.path.exists(os.path.join(self.path,"slab.xyz")): #wait until slab optimizes, this is required anyway and makes the rest of the code simpler
                time.sleep(1)

        self.slab = read(self.slab_path)

        #adsorbate optimization
        self.setup_adsorbates()

        #setup transition states
        self.setup_transition_states()

        wf = Workflow(self.fws, name="pynta")
        self.launchpad.add_wf(wf)

        self.rapidfire()
