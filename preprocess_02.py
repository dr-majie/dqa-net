import argparse
import os
import json
import shutil
from collections import defaultdict
from copy import deepcopy
from pprint import pprint
import re

import numpy as np
from nltk.stem import PorterStemmer

# from qa2hypo import qa2hypo
from utils import get_pbar


stemmer = PorterStemmer()

def qa2hypo(question, answer):
    return "%s %s" % (question, answer)

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir")
    parser.add_argument("target_dir")
    parser.add_argument("--min_count", type=int, default=5)
    parser.add_argument("--train_dir", default="")
    parser.add_argument("--vgg_model_path", default="~/caffe-models/vgg-19.caffemodel")
    parser.add_argument("--vgg_proto_path", default="~/caffe-models/vgg-19.prototxt")
    return parser.parse_args()


def _tokenize(raw):
    tokens = re.findall(r"[\w]+", raw)
    return tokens


def _vadd(vocab_counter, word):
    word = word.lower()
    word = stemmer.stem(word)
    vocab_counter[word] += 1


def _vget(vocab_dict, word):
    word = word.lower()
    word = stemmer.stem(word)
    return vocab_dict[word] if word in vocab_dict else 0


def _vlup(vocab_dict, words):
    return [_vget(vocab_dict, word) for word in words]


def _get_text(vocab_dict, anno, key):
    if key[0] == 'T':
        value = anno['text'][key]['value']
        repText = anno['text'][key]['replacementText']
        return _vlup(vocab_dict, _tokenize(value)), _vlup(vocab_dict, _tokenize(repText))
    elif key[0] == 'O':
        if 'text' in anno['objects'][key]:
            new_key = anno['objects'][key]['text'][0]
            return _get_text(vocab_dict, anno, new_key)
    return [], []


def _get_center(anno, key):
    type_dict = {'T': 'text', 'A': 'arrows', 'B': 'blobs', 'H': 'arrowHeads', 'R': 'regions', 'O': 'objects'}
    poly_dict = {'T': 'rectangle', 'A': 'polygon', 'B': 'polygon', 'H': 'rectangle', 'R': 'polygon'}
    type_ = type_dict[key[0]]
    if type_ == 'objects':
        if 'blobs' in anno[type_][key] and len(anno[type_][key]['blobs']) > 0:
            new_key = anno[type_][key]['blobs'][0]
        elif 'text' in anno[type_][key]:
            new_key = anno[type_][key]['text'][0]
        else:
            raise Exception("%r" % anno)
        return _get_center(anno, new_key)
    shape = poly_dict[key[0]]
    poly = np.array(anno[type_][key][shape])
    center = map(int, map(round, np.mean(poly, 0)))
    return center


def _get_head_center(anno, arrow_key):
    if len(anno['arrows'][arrow_key]['arrowHeads']) == 0:
        return [0, 0]
    head_key = anno['arrows'][arrow_key]['arrowHeads'][0]
    return _get_center(anno, head_key)


def _get_1hot_vector(dim, idx):
    arr = [0] * dim
    arr[idx] = 1
    return arr


def prepro_annos(args):
    """
    for each annotation file,
    [{'type': type_num,
      'r0': rect,
      'r1': rect,
      'rh': rect,
      'ra': rect,
      't0': indexed_words,
      't1': indexed_words}]

    type_num: [intraLabel, intraRegionLabel, interLinkage, intraLinkage,] (arrowDescriptor, arrowHeadTail)
    :param args:
    :return:
    """
    data_dir = args.data_dir
    target_dir = args.target_dir
    vocab_path = os.path.join(target_dir, "vocab.json")
    vocab = json.load(open(vocab_path, "r"))
    relations_path = os.path.join(target_dir, "image_relations.json")
    meta_data_path = os.path.join(target_dir, "meta_data.json")
    meta_data = json.load(open(meta_data_path, "r"))

    meta_data['pred_size'] = 2 * 4

    relations_dict = {}
    dim = 4
    hot_index_dict = {('intraObject', 'label', 'objectDescription'): 0,
                      ('intraObject', 'label', 'regionDescriptionNoArrow'): 1,
                      ('interObject', 'linkage', 'objectToObject'): 2,
                      ('intraObject', 'linkage', 'regionDescription'): 3,
                      ('intraObject', 'linkage', 'objectDescription'): 3,
                      ('intraObject', 'textLinkage', 'textDescription'): 3}

    annos_dir = os.path.join(data_dir, "annotations")
    anno_names = os.listdir(annos_dir)
    max_label_size = 0
    max_num_rels = 0
    pbar = get_pbar(len(anno_names))
    pbar.start()
    for i, anno_name in enumerate(anno_names):
        image_name, _ = os.path.splitext(anno_name)
        image_id, _ = os.path.splitext(image_name)
        anno_path = os.path.join(annos_dir, anno_name)
        anno = json.load(open(anno_path, "r"))
        relations = []
        if 'relationships' not in anno:
            relations_dict[image_id] = relations
            pbar.update(i)
            continue
        for rel_type, d in anno['relationships'].iteritems():
            for rel_subtype, dd in d.iteritems():
                if len(dd) == 0:
                    continue
                for rel_key, ddd in dd.iteritems():
                    category = ddd['category']
                    # FIXME : just choose one for now
                    origin_key = ddd['origin'][0]
                    dest_key = ddd['destination'][0]
                    origin_center = _get_center(anno, origin_key)
                    dest_center = _get_center(anno, dest_key)
                    if 'connector' in ddd:
                        arrow_key = ddd['connector'][0]
                        arrow_center = _get_center(anno, arrow_key)
                        head_center = _get_head_center(anno, arrow_key)
                    else:
                        arrow_center = [0, 0]
                        head_center = [0, 0]
                    idx = hot_index_dict[(rel_type, rel_subtype, category)]
                    # type_ = _get_1hot_vector(dim, idx)
                    type_ = idx
                    origin_text, origin_rep = _get_text(vocab, anno, origin_key)
                    dest_text, dest_rep = _get_text(vocab, anno, dest_key)
                    max_label_size = max(max_label_size, len(origin_text), len(dest_text))
                    # relation = dict(type=type_, l0=origin_center, l1=dest_center, lh=head_center, la=arrow_center, t0=origin_text, t1=dest_text)
                    pred = origin_center + dest_center + head_center + arrow_center
                    assert len(pred) == meta_data['pred_size'], "Wrong predicate size: %d" % len(pred)
                    relation = dict(a1=origin_text, pred=pred, a2=dest_text, a1r=origin_rep, a2r=dest_rep)
                    relations.append(relation)
        # TODO : arrow relations as well?
        relations_dict[image_id] = relations
        max_num_rels = max(max_num_rels, len(relations))
        pbar.update(i)
    pbar.finish()
    meta_data['max_label_size'] = max_label_size
    meta_data['max_num_rels'] = max_num_rels

    print("number of relations: %d" % sum(len(relations) for relations in relations_dict))
    print('max label size: %d' % max_label_size)
    print("max num rels: %d" % max_num_rels)
    print("dumping json file ... ")
    json.dump(relations_dict, open(relations_path, 'wb'))
    json.dump(meta_data, open(meta_data_path, 'wb'))
    print("done")


def prepro_questions(args):
    data_dir = args.data_dir
    target_dir = args.target_dir
    questions_dir = os.path.join(data_dir, "questions")
    images_dir = os.path.join(data_dir, "images")
    annos_dir = os.path.join(data_dir, "annotations")
    sents_path = os.path.join(target_dir, "sents.json")
    answer_path = os.path.join(target_dir, "answers.json")
    id_map_path = os.path.join(target_dir, "id_map.json")
    replaced_path = os.path.join(target_dir, "replaced.json")
    vocab_path = os.path.join(target_dir, "vocab.json")
    meta_data_path = os.path.join(target_dir, "meta_data.json")
    image_relations_path = os.path.join(target_dir, "image_relations.json")
    relations_path = os.path.join(target_dir, "relations.json")
    vocab = json.load(open(vocab_path, "r"))
    meta_data = json.load(open(meta_data_path, "r"))
    image_relations_dict = json.load(open(image_relations_path, "r"))

    sents_dict = {}
    answer_dict = {}
    id_map = {}
    replaced = {}
    relations_dict = {}

    ques_names = os.listdir(questions_dir)
    question_id = 0
    max_sent_size = 0
    pbar = get_pbar(len(ques_names))
    pbar.start()
    num_choices = 0
    for i, ques_name in enumerate(ques_names):
        image_name, ext = os.path.splitext(ques_name)
        if ext != ".json":
            pbar.update(i)
            continue
        image_id, _ = os.path.splitext(image_name)
        relations = image_relations_dict[image_id]
        ques_path = os.path.join(questions_dir, ques_name)
        anno_path = os.path.join(annos_dir, ques_name)
        image_path = os.path.join(images_dir, image_name)
        assert os.path.exists(anno_path), "%s does not exist." % anno_path
        assert os.path.exists(image_path), "%s does not exist." % image_path
        ques = json.load(open(ques_path, "r"))
        for ques_text, d in ques['questions'].iteritems():
            """
            ques_words = _tokenize(ques_text)
            choice_wordss = [_tokenize(choice) for choice in d['answerTexts']]
            sents = [_vlup(vocab, ques_words + choice_words) for choice_words in choice_wordss]
            """
            sents = [_vlup(vocab, _tokenize(qa2hypo(ques_text, choice))) for choice in d['answerTexts']]
            assert not num_choices or num_choices == len(sents), "number of choices don't match: %s" % ques_name
            num_choices = len(sents)
            # TODO : one hot vector or index?
            sents_dict[str(question_id)] = sents
            answer_dict[str(question_id)] = d['correctAnswer']
            id_map[str(question_id)] = image_id
            max_sent_size = max(max_sent_size, max(len(sent) for sent in sents))
            replaced[str(question_id)] = d['abcLabel']
            new_relations = deepcopy(relations)
            if d['abcLabel']:
                for relation in new_relations:
                    relation['a1'] = relation['a1r']
                    relation['a2'] = relation['a2r']
            relations_dict[str(question_id)] = new_relations
            question_id += 1
        pbar.update(i)
    pbar.finish()
    meta_data['max_sent_size'] = max_sent_size
    meta_data['num_choices'] = num_choices

    print("number of questions: %d" % len(sents_dict))
    print("number of choices: %d" % num_choices)
    print("max sent size: %d" % max_sent_size)
    print("dumping json file ... ")
    json.dump(sents_dict, open(sents_path, "wb"))
    json.dump(answer_dict, open(answer_path, "wb"))
    json.dump(id_map, open(id_map_path, "wb"))
    json.dump(meta_data, open(meta_data_path, "wb"))
    json.dump(replaced, open(replaced_path, "wb"))
    json.dump(relations_dict, open(relations_path, "wb"))
    print("done")


def build_vocab(args):
    if args.train_dir:
        vocab_path = os.path.join(args.train_dir, "vocab.json")
        new_vocab_path = os.path.join(args.target_dir, "vocab.json")
        print("using vocab from %s" % vocab_path)
        shutil.copy(vocab_path, new_vocab_path)
        return
    data_dir = args.data_dir
    target_dir = args.target_dir
    min_count = args.min_count
    vocab_path = os.path.join(target_dir, "vocab.json")
    questions_dir = os.path.join(data_dir, "questions")
    annos_dir = os.path.join(data_dir, "annotations")

    vocab_counter = defaultdict(int)
    anno_names = os.listdir(annos_dir)
    pbar = get_pbar(len(anno_names))
    pbar.start()
    for i, anno_name in enumerate(anno_names):
        if os.path.splitext(anno_name)[1] != ".json":
            pbar.update(i)
            continue
        anno_path = os.path.join(annos_dir, anno_name)
        anno = json.load(open(anno_path, "r"))
        for _, d in anno['text'].iteritems():
            text = d['value']
            for word in _tokenize(text):
                _vadd(vocab_counter, word)
        pbar.update(i)
    pbar.finish()

    ques_names = os.listdir(questions_dir)
    pbar = get_pbar(len(ques_names))
    pbar.start()
    for i, ques_name in enumerate(ques_names):
        if os.path.splitext(ques_name)[1] != ".json":
            pbar.update(i)
            continue
        ques_path = os.path.join(questions_dir, ques_name)
        ques = json.load(open(ques_path, "r"))
        for ques_text, d in ques['questions'].iteritems():
            for word in _tokenize(ques_text):
                _vadd(vocab_counter, word)
            for choice in d['answerTexts']:
                for word in _tokenize(choice):
                    _vadd(vocab_counter, word)
        pbar.update(i)
    pbar.finish()

    vocab_list, counts = zip(*sorted([pair for pair in vocab_counter.iteritems() if pair[1] > min_count],
                             key=lambda x: -x[1]))

    freq = 5
    print("top %d frequent words:" % freq)
    for word, count in zip(vocab_list[:freq], counts[:freq]):
        print("%r: %d" % (word, count))

    vocab_dict = {word: idx+1 for idx, word in enumerate(sorted(vocab_list))}
    vocab_dict['UNK'] = 0
    print("vocab size: %d" % len(vocab_dict))
    print ("dumping json file ... ")
    json.dump(vocab_dict, open(vocab_path, "wb"))
    print ("done")


def create_meta_data(args):
    target_dir = args.target_dir
    if not os.path.exists(target_dir):
        os.mkdir(target_dir)
    meta_data_path = os.path.join(target_dir, "meta_data.json")
    meta_data = {'data_dir': args.data_dir}
    json.dump(meta_data, open(meta_data_path, "wb"))


def create_image_ids_and_paths(args):
    data_dir = args.data_dir
    target_dir = args.target_dir
    images_dir = os.path.join(data_dir, "images")
    image_ids_path = os.path.join(target_dir, "image_ids.json")
    image_paths_path = os.path.join(target_dir, "image_paths.json")
    image_names = [name for name in os.listdir(images_dir) if name.endswith(".png")]
    image_ids = [os.path.splitext(name)[0] for name in image_names]
    ordered_image_ids = sorted(image_ids, key=lambda x: int(x))
    ordered_image_names = ["%s.png" % id_ for id_ in ordered_image_ids]
    print ("dumping json files ... ")
    image_paths = [os.path.join(images_dir, name) for name in ordered_image_names]
    json.dump(ordered_image_ids, open(image_ids_path, "wb"))
    json.dump(image_paths, open(image_paths_path, "wb"))
    print ("done")


def prepro_images(args):
    model_path = args.vgg_model_path
    proto_path = args.vgg_proto_path
    out_path = os.path.join(args.target_dir, "images.h5")
    image_paths_path = os.path.join(args.target_dir, "image_paths.json")
    os.system("th prepro_images.lua --image_path_json %s --cnn_proto %s --cnn_model %s --out_path %s"
              % (image_paths_path, proto_path, model_path, out_path))


if __name__ == "__main__":
    ARGS = get_args()
    create_meta_data(ARGS)
    create_image_ids_and_paths(ARGS)
    prepro_images(ARGS)
    build_vocab(ARGS)
    prepro_annos(ARGS)
    prepro_questions(ARGS)
