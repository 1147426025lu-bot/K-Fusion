DIRS = src

.PHONY: all

all:   ktcutil ktcoption native

KTC_PKGS = cil,yojson,csv
KTC_CFLAGS = -w -a -thread
KTC_LFLAGS = -thread -package findlib -package cil -package cil.callgraph -package cil.liveness

ktcutil:
	@rm -f -r libs
	@mkdir libs
	@ocamlbuild -no-hygiene -cflags '$(KTC_CFLAGS)' -use-ocamlfind -pkgs '$(KTC_PKGS)' -Is $(DIRS) ktcutil.cma
	@ocamlbuild   -no-hygiene -cflags '$(KTC_CFLAGS)' -use-ocamlfind -pkgs '$(KTC_PKGS)' -Is $(DIRS) ktcutil.cmxa
	@rm -f bytes.ml
	@cp _build/src/ktcutil.cma libs/.
	@cp _build/src/ktcutil.cmxa libs/.

ktcoption:
	@ocamlbuild -cflags '$(KTC_CFLAGS)'  -no-hygiene -use-ocamlfind -pkgs '$(KTC_PKGS)'  -Is $(DIRS) ktcoptions.cma > log
	@ocamlbuild  -cflags '$(KTC_CFLAGS)' -no-hygiene -use-ocamlfind -pkgs '$(KTC_PKGS)'  -Is $(DIRS) ktcoptions.cmxa > log
	@ocamlbuild  -cflags '-w -a' -no-hygiene -use-ocamlfind -pkgs '$(KTC_PKGS)'  -Is $(DIRS) cilktc.cma > log
	@ocamlbuild  -cflags '$(KTC_CFLAGS)' -no-hygiene -use-ocamlfind -pkgs '$(KTC_PKGS)' -Is $(DIRS) cilktc.cmxa
	@rm -f bytes.ml
	@cp _build/src/ktcoptions.cma libs/.
	@cp _build/src/ktcoptions.cmxa libs/.

native:
	@ocamlbuild -cflags '$(KTC_CFLAGS)' -lflags '$(KTC_LFLAGS)' -no-hygiene -use-ocamlfind -pkgs '$(KTC_PKGS)' -Is $(DIRS) main.native
	@rm -f main.native
	@cd bin; cp ../_build/src/main.native ktcexe

clean:
	@rm -f -r libs
	@rm -f -r _build
	@rm -f bin/ktcexe
	@rm -f bin/*.cil.c
	@rm -f bin/*.i


