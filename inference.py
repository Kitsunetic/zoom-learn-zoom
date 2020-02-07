import yaml
from PIL import Image
import tensorflow as tf
import glob, os
import numpy as np
import net as net
import utils as utils

def main():
  config_file_path = "config/inference.yaml"
  with open(config_file_path, "r") as f:
    config_file = yaml.load(f)

  # Model parameters
  mode = config_file["mode"]
  device = config_file["device"]
  up_ratio = config_file["model"]["up_ratio"]
  num_in_ch = config_file["model"]["num_in_channel"]
  num_out_ch = config_file["model"]["num_out_channel"]
  file_type = config_file["model"]["file_type"]
  upsample_type = config_file["model"]["upsample_type"]
  
  # Input / Output parameters
  inference_root = [config_file["io"]["inference_root"]]
  task_folder = config_file["io"]["task_folder"]
  restore_path = config_file["io"]["restore_ckpt"]

  # Remove boundary pixels with artifacts
  raw_tol = 4
  
  # read in white and black level to normalize raw sensor data for different devices
  # normalize하기 위해 각 device 고유의 white/black level 값을 구한다.
  white_lv, black_lv = utils.read_wb_lv(device)

  # set up the model
  with tf.variable_scope(tf.get_variable_scope()):
    input_raw = tf.placeholder(tf.float32, shape=[1, None, None, num_in_ch], name="input_raw")
    out_rgb = net.SRResnet(input_raw, num_out_ch, up_ratio=up_ratio, reuse=False, up_type=upsample_type)

    if raw_tol != 0:
      out_rgb = out_rgb[
        :,
        int(raw_tol/2)*(up_ratio*4):-int(raw_tol/2)*(up_ratio*4),
        int(raw_tol/2)*(up_ratio*4):-int(raw_tol/2)*(up_ratio*4),
        :
      ] # add a small offset to deal with boudary case

    #print('out_rgb.shape:', out_rgb.shape)
    #exit()
    """
    out_rgb.shape: (1, ?, ?, 3)
    """
    
    objDict = {}
    objDict['out_rgb'] = out_rgb

  ###################################### Session
  with tf.Session() as sess:
    merged = tf.summary.merge_all()
    saver_restore = tf.train.Saver([var for var in tf.trainable_variables()])

    sess.run(tf.global_variables_initializer())
    ckpt=tf.train.get_checkpoint_state("%s"%(restore_path))
    if not ckpt:
      raise FileNotFoundError("No checkpoint found")
    else:
      print("Contain checkpoint: ", ckpt)
      saver_restore.restore(sess, ckpt.model_checkpoint_path)

    # print model summary
    from tensorflow.python.tools.inspect_checkpoint import print_tensors_in_checkpoint_file
    print_tensors_in_checkpoint_file(file_name='%s/model.ckpt'%(restore_path), tensor_name='', all_tensors=False)

    if mode == 'inference':
      inference_paths = utils.read_paths(inference_root, type=file_type)
      num_test = len(inference_paths)
    elif mode == 'inference_single':
      inference_paths = [config_file["io"]['inference_path']]
      num_test = 1
    
    if not os.path.isdir("%s/%s"%(task_folder, mode)):
      os.makedirs("%s/%s"%(task_folder, mode))

    for id, inference_path in enumerate(inference_paths):
      print("Inference on %d image."%(id+1))
      crop_ratio_list = [8]
      fracx_list = [config_file["io"]["fracx"]]
      fracy_list = [config_file["io"]["fracy"]]
      # save_prefix = config_file["io"]["prefix"] # 0.35,0.45,0.55,0.65,0.75
      for idx, fracx in enumerate(fracx_list): 
        for idy, fracy in enumerate(fracy_list):
          save_prefix = "%d-%d-%d"%(id, idx, idy)
          for crop_ratio in crop_ratio_list:
            resize_ratio = crop_ratio / 10. # resize outputs to a reasonable size

            prefix = os.path.basename(os.path.dirname(inference_path))

            if not os.path.isdir("%s/%s"%(task_folder, mode)):
              os.makedirs("%s/%s"%(task_folder, mode))

            if not os.path.isdir("%s/%s/%s-s%d"%(task_folder, mode, prefix, crop_ratio)):
              os.makedirs("%s/%s/%s-s%d"%(task_folder, mode, prefix, crop_ratio))

            # 출력 이미지의 색조 normalization을 하기 위한 값 획득.
            #     (현재는 wb.txt 파일이 없으니깐 입력 이미지에서 직접 계산한다.)
            wb_txt = os.path.dirname(inference_path) + '/wb.txt'
            if os.path.isfile(wb_txt):
              out_wb = utils.read_wb(wb_txt, key=os.path.basename(inference_path).split('.')[0] + ":")
            else:
              print("white balance txt not exist, reading from raw EXIF data ... ")
              out_wb = utils.compute_wb(inference_path)

            input_bayer = utils.get_bayer(inference_path, black_lv, white_lv)
            #print('inference_path:', inference_path)
            #print('inference_path:', black_lv)
            #print('inference_path:', white_lv)
            #print('input_bayer:', input_bayer.shape)
            #exit()
            """
            # Total number of params: 14585292
            Inference on 1 image.
            white balance txt not exist, reading from raw EXIF data ... 
            Computing WB for ./quick_inference/00134.ARW
            inference_path: ./quick_inference/00134.ARW
            inference_path: 512
            inference_path: 16383
            input_bayer: (2848, 4256) --> 원본 RAW 이미지 사이즈(1채널) 인 듯함
            """
            input_raw_reshape = utils.reshape_raw(input_bayer)
            input_raw_img_orig = utils.crop_fov_free(input_raw_reshape, 1./crop_ratio, crop_fracx=fracx, crop_fracy=fracy)

            # loss 계산을 위해 GroundTruth JPG 파일을 읽는다.
            rgb_camera_path = inference_path.replace(".ARW", ".JPG")
            rgb_camera =  np.array(Image.open(rgb_camera_path))
            cropped_input_rgb = utils.crop_fov_free(rgb_camera, 1./crop_ratio, crop_fracx=fracx, crop_fracy=fracy)
            cropped_input_rgb = utils.image_float(cropped_input_rgb)

            print("Testing on image : %s"%(inference_path), input_raw_img_orig.shape)

            input_raw_img = np.expand_dims(input_raw_img_orig, 0)
            out_objDict = sess.run(objDict, feed_dict={input_raw: input_raw_img})
            
            wb_rgb = out_objDict["out_rgb"][0, ...]
            #print('wb_rgb.shape:', wb_rgb.shape)
            #exit()
            """
            wb_rgb.shape: (1360, 2064, 3)
            """

            # 만들어진 출력 RGB 이미지 색조 normalization
            wb_rgb[..., 0] *= np.power(out_wb[0, 0], 1/2.2)
            wb_rgb[..., 1] *= np.power(out_wb[0, 1], 1/2.2)
            wb_rgb[..., 2] *= np.power(out_wb[0, 3], 1/2.2)
            
            print("Saving outputs ... ")
            # raw파일에서 신경망을 타고 변환된 RGB 파일 저장
            output_rgb = Image.fromarray(np.uint8(utils.clipped(wb_rgb)*255))
            output_rgb = output_rgb.resize((
                int(output_rgb.width * resize_ratio),
                int(output_rgb.height * resize_ratio)), Image.ANTIALIAS)
            output_rgb.save("%s/%s/%s-s%d/out_rgb_%s.png"%(task_folder, mode, prefix, crop_ratio, save_prefix))

            # 원본 RGB 파일 저장
            input_camera_rgb = Image.fromarray(np.uint8(utils.clipped(cropped_input_rgb)*255))
            input_camera_rgb.save("%s/%s/%s-s%d/input_rgb_camera_orig_%s.png"%(task_folder,mode,prefix,crop_ratio,save_prefix))
            input_camera_rgb_naive = input_camera_rgb.resize((
                int(input_camera_rgb.width * up_ratio),
                int(input_camera_rgb.height * up_ratio)), Image.ANTIALIAS)
            input_camera_rgb_naive.save("%s/%s/%s-s%d/input_rgb_camera_naive_%s.png"%(task_folder,mode,prefix,crop_ratio,save_prefix), compress_level=1)

if __name__ == "__main__":
  main()
