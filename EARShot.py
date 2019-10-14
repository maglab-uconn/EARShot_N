import numpy as np
import tensorflow as tf
import _pickle as pickle
from tensorflow.contrib.seq2seq import BasicDecoder, TrainingHelper, dynamic_decode
from tensorflow.contrib.rnn import LSTMCell, GRUCell, BasicRNNCell, LSTMStateTuple
from ZoneoutLSTMCell import ZoneoutLSTMCell
from threading import Thread
import time, os, sys, argparse
import matplotlib
matplotlib.use('agg')
import matplotlib.pyplot as plt
from Pattern_Feeder import Pattern_Feeder
from SCRNCell import SCRNCell, SCRNStateTuple
from Hyper_Parameters import pattern_Parameters, model_Parameters
try: import ctypes
except: pass


class EARShot_Model:
    #Initialize the model
    def __init__(
        self,
        start_Epoch,
        excluded_Talker,
        extract_Dir
        ):
        try: ctypes.windll.kernel32.SetConsoleTitleW(extract_Dir.replace("_", ":").replace(".", " "))   #If you run this script in the linux or mac, remove this line.
        except: pass
        self.extract_Dir = extract_Dir

        self.tf_Session = tf.Session()        
        
        #Pattern data is generated from other thread.
        self.pattern_Feeder = Pattern_Feeder(
            excluded_Talker = excluded_Talker,
            start_Epoch = start_Epoch,
            metadata_File = os.path.join(extract_Dir, "Result", "Metadata.pickle").replace("\\", "/") if start_Epoch > 0 else None
            )

        self.Tensor_Generate()

        self.tf_Saver = tf.train.Saver(max_to_keep=0)

        extract_Metadata_Thread = Thread(target=self.Extract_Metadata)
        extract_Metadata_Thread.daemon = True
        extract_Metadata_Thread.start()
        extract_Metadata_Thread.join()
            
    #Tensor making for training and test
    def Tensor_Generate(self):
        if pattern_Parameters.Pattern_Use_Bit == 16:
            float_Bit_Type = tf.float16
            int_Bit_Type = tf.int16
        elif pattern_Parameters.Pattern_Use_Bit == 32:
            float_Bit_Type = tf.float32
            int_Bit_Type = tf.int32
        else:
            assert False

        placeholder_Dict = self.pattern_Feeder.placeholder_Dict

        with tf.variable_scope('EARS') as scope:
            batch_Size = tf.shape(placeholder_Dict["Acoustic"])[0]

            input_Activation = placeholder_Dict["Acoustic"]
            conv_Parameters = enumerate(zip(
                model_Parameters.Prenet_Conv.Channels,
                model_Parameters.Prenet_Conv.Kernel_Sizes,
                model_Parameters.Prenet_Conv.Strides
                ))

            if model_Parameters.Prenet_Conv.Use:
                for conv_Index, (channel, kernel_Size, stride) in conv_Parameters:
                    with tf.variable_scope('Prenet_Conv_{}'.format(conv_Index)):
                        input_Activation = tf.layers.conv1d(
                            inputs=input_Activation,
                            filters= channel,
                            kernel_size= kernel_Size,
                            strides= stride,
                            padding='same',
                            activation= tf.nn.relu
                            )
                        input_Activation = tf.layers.batch_normalization(
                            inputs=input_Activation,
                            training= placeholder_Dict["Is_Training"]
                            )
                        if not model_Parameters.Prenet_Conv.Dropout_Rate is None:
                            input_Activation = tf.layers.dropout(
                                input_Activation,
                                rate= model_Parameters.Prenet_Conv.Dropout_Rate,
                                training= placeholder_Dict["Is_Training"]
                                )

            #This model use only training helper.
            helper = TrainingHelper(
                inputs= placeholder_Dict["Acoustic"],
                sequence_length = placeholder_Dict["Length"]
                )

            #RNN. Model can select four types hidden.
            #Previous RNN state is for the no reset.       
            if model_Parameters.Hidden_Type in ["LSTM", 'ZoneoutLSTM']:
                if model_Parameters.Hidden_Type == "LSTM":
                    rnn_Cell = LSTMCell(model_Parameters.Hidden_Size)
                elif model_Parameters.Hidden_Type == "ZoneoutLSTM":             
                    rnn_Cell = ZoneoutLSTMCell(
                        num_units= model_Parameters.Hidden_Size,
                        is_training= placeholder_Dict["Is_Training"],
                        cell_zoneout_rate= model_Parameters.Zoneout_Rate,
                        output_zoneout_rate= model_Parameters.Zoneout_Rate
                        )
                previous_RNN_State = tf.Variable(
                    initial_value = LSTMStateTuple(
                        c = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size)), 
                        h = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size))
                        ),
                    trainable = False,
                    dtype= float_Bit_Type
                    )
                decoder_Initial_State = LSTMStateTuple(
                    c=previous_RNN_State[0][:batch_Size],
                    h=previous_RNN_State[1][:batch_Size]
                    )
            elif model_Parameters.Hidden_Type == "SCRN":
                rnn_Cell = SCRNCell(model_Parameters.Hidden_Size)
                previous_RNN_State = tf.Variable(
                    initial_value = SCRNStateTuple(
                        s = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size)), 
                        h = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size))
                        ),
                    trainable = False,
                    dtype= float_Bit_Type
                    )
                decoder_Initial_State = SCRNStateTuple(
                    s=previous_RNN_State[0][:batch_Size],
                    h=previous_RNN_State[1][:batch_Size]
                    )
            elif model_Parameters.Hidden_Type in ["GRU", "BPTT"]:
                if model_Parameters.Hidden_Type == "GRU":
                    rnn_Cell = GRUCell(model_Parameters.Hidden_Size)
                elif model_Parameters.Hidden_Type == "BPTT":
                    rnn_Cell = BasicRNNCell(model_Parameters.Hidden_Size)
                previous_RNN_State = tf.Variable(
                    initial_value = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size)),
                    trainable = False,
                    dtype= float_Bit_Type
                    )
                decoder_Initial_State = previous_RNN_State[:batch_Size]

            decoder = BasicDecoder(
                cell=rnn_Cell, 
                helper=helper, 
                initial_state=decoder_Initial_State
                )

            outputs, final_State, _ = dynamic_decode(
                decoder = decoder,
                output_time_major = False,
                impute_finished = True
                )
            
            hidden_Activation = outputs.rnn_output

            #Semantic   (hidden_size -> semantic_size)
            semantic_Logits = tf.layers.dense(
                inputs = hidden_Activation,
                units = self.pattern_Feeder.semantic_Size,                
                use_bias=True,
                name = "semantic_Logits"
                )

        #Back-prob.
        with tf.variable_scope('training_Loss') as scope:
            loss_Mask = tf.sequence_mask(placeholder_Dict["Length"], dtype=tf.float32)

            loss_Calculation = tf.nn.sigmoid_cross_entropy_with_logits(                
                labels = placeholder_Dict["Semantic"],
                logits = semantic_Logits
                )
            loss_Calculation = tf.reduce_mean(loss_Calculation, axis=-1)
            loss_Calculation *= loss_Mask
            
            loss = tf.reduce_sum(loss_Calculation)

            if model_Parameters.Weight_Regularization.Use:
                loss += model_Parameters.Weight_Regularization.Rate * tf.reduce_sum([
                    tf.nn.l2_loss(variable)
                    for variable in tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)
                    if not any([keyword.lower() in variable.name.lower() for keyword in model_Parameters.Weight_Regularization.Except_Keywords])
                    ])

            loss_Display = tf.reduce_sum(loss_Calculation, axis=0) / tf.math.count_nonzero(loss_Calculation, axis=0, dtype=tf.float32)    #This is for the display. There is no meaning.
            
            global_Step = tf.Variable(0, name='global_Step', trainable = False)

            ##Noam decay of learning rate
            step = tf.cast(global_Step + 1, dtype=float_Bit_Type)
            warmup_Steps = 4000.0
            learning_Rate = model_Parameters.Learning_Rate * warmup_Steps ** 0.5 * tf.minimum(step * warmup_Steps**-1.5, step**-0.5)

            #Static(Temp)
            #learning_Rate = tf.cast(model_Parameters.Learning_Rate, float_Bit_Type)

            #Weight update. We use the ADAM optimizer
            optimizer = tf.train.AdamOptimizer(learning_Rate)
            gradients, variables = zip(*optimizer.compute_gradients(loss))
            clipped_Gradients, global_Norm = tf.clip_by_global_norm(gradients, 1.0)
            optimize = optimizer.apply_gradients(zip(clipped_Gradients, variables), global_step=global_Step)

            #For no reset. Model save the rnn states.
            if model_Parameters.Hidden_Type in ["LSTM", 'ZoneoutLSTM']:
                rnn_State_Assign = tf.assign(
                    ref= previous_RNN_State,
                    value = LSTMStateTuple(
                       c = tf.concat([final_State[0][:batch_Size], previous_RNN_State[0][batch_Size:]], axis = 0),
                       h = tf.concat([final_State[1][:batch_Size], previous_RNN_State[1][batch_Size:]], axis = 0)
                       )
                    )
            if model_Parameters.Hidden_Type == "SCRN":
                rnn_State_Assign = tf.assign(
                    ref= previous_RNN_State,
                    value = SCRNStateTuple(
                       s = tf.concat([final_State[0][:batch_Size], previous_RNN_State[0][batch_Size:]], axis = 0),
                       h = tf.concat([final_State[1][:batch_Size], previous_RNN_State[1][batch_Size:]], axis = 0)
                       )
                    )
            elif model_Parameters.Hidden_Type in ["GRU", "BPTT"]:
                rnn_State_Assign = tf.assign(
                    ref= previous_RNN_State,
                    value = tf.concat([final_State[:batch_Size], previous_RNN_State[batch_Size:]], axis = 0)
                    )

        with tf.variable_scope('test') as scope:
            #In test, previous hidden state should be zero. Thus, the saved values should be backup and become zero.
            if model_Parameters.Hidden_Type in ["LSTM", 'ZoneoutLSTM']:
                backup_RNN_State = tf.Variable(
                    initial_value = LSTMStateTuple(
                        c = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size)), 
                        h = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size))
                        ),
                    trainable = False,
                    dtype= float_Bit_Type
                    )
            elif model_Parameters.Hidden_Type == "SCRN":
                backup_RNN_State = tf.Variable(
                    initial_value = SCRNStateTuple(
                        s = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size)), 
                        h = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size))
                        ),
                    trainable = False,
                    dtype= float_Bit_Type
                    )
            elif model_Parameters.Hidden_Type in ["GRU", "BPTT"]:
                backup_RNN_State = tf.Variable(
                    initial_value = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size)),
                    trainable = False,
                    dtype= float_Bit_Type
                    )

            backup_RNN_State_Assign = tf.assign(
                ref= backup_RNN_State,
                value = previous_RNN_State
                )
            with tf.control_dependencies([backup_RNN_State_Assign]):
                if model_Parameters.Hidden_Type in ["LSTM", 'ZoneoutLSTM']:
                    zero_RNN_State_Assign = tf.assign(
                        ref= previous_RNN_State,
                        value = LSTMStateTuple(
                            c = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size), dtype = float_Bit_Type), 
                            h = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size), dtype = float_Bit_Type)
                            )
                        )
                elif model_Parameters.Hidden_Type == "SCRN":
                    zero_RNN_State_Assign = tf.assign(
                        ref= previous_RNN_State,
                        value = LSTMStateTuple(
                            s = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size), dtype = float_Bit_Type), 
                            h = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size), dtype = float_Bit_Type)
                            )
                        )
                elif model_Parameters.Hidden_Type in ["GRU", "BPTT"]:
                    zero_RNN_State_Assign = tf.assign(
                        ref= previous_RNN_State,
                        value = tf.zeros(shape=(model_Parameters.Batch_Size, model_Parameters.Hidden_Size), dtype = float_Bit_Type)
                        )
            
            restore_RNN_State_Assign = tf.assign(
                ref= previous_RNN_State,
                value = backup_RNN_State
                )

            semantic_Activation = tf.nn.sigmoid(semantic_Logits)
                    
        self.training_Tensor_List = [global_Step, learning_Rate, loss_Display, optimize, rnn_State_Assign]
        
        self.test_Mode_Turn_On_Tensor_List = [backup_RNN_State_Assign, zero_RNN_State_Assign]  #Hidden state backup
        self.test_Mode_Turn_Off_Tensor_List = [restore_RNN_State_Assign]   #Hidden state restore

        self.test_Tensor_List = [global_Step, semantic_Activation] #In test, we only need semantic activation
        
        self.hidden_Plot_Tensor_List = [tf.transpose(hidden_Activation, perm=[0, 2, 1])]   #In hidden analysis, we only need hidden activation.

        self.tf_Session.run(tf.global_variables_initializer()) #Initialize the weights
        
    #Checkpoint load
    def Restore(self, warning_Ignore = False):
        if not os.path.exists(self.extract_Dir + "/Checkpoint"):
            print("There is no checkpoint.")
            return
        if not warning_Ignore:
            latest_Checkpoint = tf.train.latest_checkpoint(self.extract_Dir + "/Checkpoint")
            print("Lastest checkpoint:", latest_Checkpoint)
            if latest_Checkpoint is not None:
                latest_Trained_Epoch = int(latest_Checkpoint[latest_Checkpoint.index("Checkpoint-") + 11:])
                if latest_Trained_Epoch > self.pattern_Feeder.start_Epoch:
                    try:
                        input("\n".join([
                        "WARNING!",
                        "THE START EPOCH IS SMALLER THAN THE TRAINED MODEL.",
                        "CHANGE THE START EPOCH OR THE FOLDER NAME OF PREVIOUS MODEL TO PREVENT TO OVERWRITE.",
                        "TO STOP, PRESS 'CTRL + C'.",
                        "TO CONTINUE, PRESS 'ENTER'.\n"
                        ]))
                    except KeyboardInterrupt:
                        print("Stopped.")
                        sys.exit()

        checkpoint = self.extract_Dir + "/Checkpoint/Checkpoint-" + str(self.pattern_Feeder.start_Epoch)
        try:
            self.tf_Saver.restore(self.tf_Session, checkpoint)
        except tf.errors.NotFoundError:
            print("here is no checkpoint about the start epoch. Stopped.")
            sys.exit()
        print("Checkpoint '", checkpoint, "' is loaded.")

    #Training
    def Train(self):
        if not os.path.exists(self.extract_Dir + "/Checkpoint"):
            os.makedirs(self.extract_Dir + "/Checkpoint")
        checkpoint_Path = self.extract_Dir + "/Checkpoint/Checkpoint"

        current_Epoch = self.pattern_Feeder.start_Epoch - 1
        while not self.pattern_Feeder.is_Finished or len(self.pattern_Feeder.pattern_Queue) > 0:    #When there is no more training pattern, the train function will be done.
            current_Epoch, is_New_Epoch, feed_Dict = self.pattern_Feeder.Get_Pattern()            

            if is_New_Epoch and current_Epoch % model_Parameters.Checkpoint_Timing == 0:
                self.tf_Saver.save(self.tf_Session, checkpoint_Path, global_step = current_Epoch)
                print("Checkpoint saved")
            if is_New_Epoch and current_Epoch % model_Parameters.Test_Timing == 0:
                self.Test(epoch=current_Epoch)

            start_Time = time.time()
            global_Step, learning_Rate, training_Loss = self.tf_Session.run(
                fetches = self.training_Tensor_List,
                feed_dict = feed_Dict
                )[:3]

            print(
                "Spent_Time:", np.round(time.time() - start_Time, 3), "\t",
                "Global_Step:", global_Step, "\t",
                "Epoch:", current_Epoch, "\t",
                "Learning_Rate:", learning_Rate, "\n",
                "Training_Loss:", " ".join(["%0.5f" % x for x in training_Loss])
                )

        #Final test and save
        self.tf_Saver.save(self.tf_Session, checkpoint_Path, global_step = current_Epoch + 1)
        print("Checkpoint saved")
        test_Thread = self.Test(epoch=current_Epoch + 1)
        
        test_Thread.join() #Wait unitl finishing the test and extract the data.

    #Test
    def Test(self, epoch):
        self.tf_Session.run(self.test_Mode_Turn_On_Tensor_List) #Backup the hidden state

        test_Feed_Dict_List = self.pattern_Feeder.Get_Test_Pattern_List()        

        for feed_Index, feed_Dict in enumerate(test_Feed_Dict_List):
            global_Step, semantic_Activation = self.tf_Session.run(
                fetches = self.test_Tensor_List,
                feed_dict = feed_Dict
                )                        
            padding_Array = np.zeros((semantic_Activation.shape[0], self.pattern_Feeder.test_Pattern_Dict["Max_Cycle"], self.pattern_Feeder.semantic_Size)) #Padding is for stacking the result data.
            padding_Array[:, :semantic_Activation.shape[1], :] = semantic_Activation
            
            extract_Thread = Thread(target=self.Extract_Result, args=(padding_Array, epoch, feed_Index * model_Parameters.Batch_Size))
            extract_Thread.daemon = True
            extract_Thread.start()


        self.tf_Session.run(self.test_Mode_Turn_Off_Tensor_List)     #Restore the hidden state
        
        return extract_Thread
             
    def Extract_Metadata(self):
        while True:
            if self.pattern_Feeder.is_Test_Pattern_Generated:
                break
            time.sleep(1.0)

        if not os.path.exists(self.extract_Dir + "/Result"):
            os.makedirs(self.extract_Dir + "/Result")

        #If there is no metadata, save the metadata
        #In metadata, there are several basic hyper parameters, and the pattern information for result analysis.        
        if not os.path.isfile(self.extract_Dir + "/Result/Metadata.pickle"):
            metadata_Dict = {}
            metadata_Dict["Acoustic_Size"] = self.pattern_Feeder.acoustic_Size
            metadata_Dict["Semantic_Size"] = self.pattern_Feeder.semantic_Size
            metadata_Dict["Hidden_Size"] = model_Parameters.Hidden_Size
            metadata_Dict["Learning_Rate"] = model_Parameters.Learning_Rate
            
            metadata_Dict["Pronunciation_Dict"] = self.pattern_Feeder.pronunciation_Dict
            #Feed_Dict_List cannot be pickled because of the placeholder.
            metadata_Dict["Test_Pattern_Dict"] = {
                key: value
                for key, value in self.pattern_Feeder.test_Pattern_Dict.items()
                if not key in ["Acoustic_Pattern", "Semantic_Pattern", "Feed_Dict_List"]
                }
            metadata_Dict["Target_Dict"] = self.pattern_Feeder.target_Dict

            metadata_Dict["Trained_Pattern_List"] = list(self.pattern_Feeder.training_Pattern_Path_Dict.keys()) #'Trained' category patterns
            metadata_Dict["Excluded_Pattern_List"] = list(self.pattern_Feeder.excluded_Pattern_Path_Dict.keys())    #'Excluded words' and 'excluded talkers' patterns
            metadata_Dict["Excluded_Talker"] = self.pattern_Feeder.excluded_Talker    #'Excluded words' and 'excluded talkers' patterns
            with open(os.path.join(self.extract_Dir, "Result", "Metadata.pickle").replace("\\", "/"), "wb") as f:
                pickle.dump(metadata_Dict, f, protocol=0)

    #Data extract
    def Extract_Result(self, semantic_Activation, epoch, start_Index):
        if not os.path.exists(self.extract_Dir + "/Result"):
            os.makedirs(self.extract_Dir + "/Result")
            
        result_Dict = {}
        result_Dict["Epoch"] = epoch
        result_Dict["Start_Index"] = start_Index
        result_Dict["Result"] = semantic_Activation
        result_Dict["Exclusion_Ignoring"] = epoch > model_Parameters.Max_Epoch_with_Exclusion and epoch <= model_Parameters.Max_Epoch_without_Exclusion
                
        with open(self.extract_Dir + "/Result/E_{:06d}.I_{:09d}.pickle".format(epoch, start_Index), "wb") as f:
            pickle.dump(result_Dict, f, protocol=0)

if __name__ == "__main__":
    argParser = argparse.ArgumentParser()
    argParser.add_argument("-se", "--start_epoch", required=False) #When you want to load the model, you should assign this parameter with 'metadata_file'. Basic is 0.
    argParser.set_defaults(start_epoch = "0")
    argParser.add_argument("-et", "--exclusion_talker", required=False) #The assigned talker's all patterns were excluded. This is only for the T or M mode. If you does not assign and model is 'T' or 'M', Model select randomly one talker.
    argParser.set_defaults(exclusion_talker = None)
    argParser.add_argument("-idx", "--index", required=False)  #This is just for identifier. This parameter does not affect the model's performance
    argParser.set_defaults(idx = None)
    argument_Dict = vars(argParser.parse_args())
    
    start_Epoch = int(argument_Dict["start_epoch"])
    exclusion_Talker = argument_Dict["exclusion_talker"]
    simulation_Index = argument_Dict["index"]

    extract_Dir_List = []
    extract_Dir_List.append("HT_{}".format(model_Parameters.Hidden_Type))
    extract_Dir_List.append("HU_{}".format(model_Parameters.Hidden_Size))    
    if not exclusion_Talker is None:
        extract_Dir_List.append("ET_{}".format(exclusion_Talker))
    if not simulation_Index is None:
        extract_Dir_List.append("IDX_{}".format(simulation_Index))
    extract_Dir = os.path.join(model_Parameters.Extract_Path, ".".join(extract_Dir_List))

    new_EARS_Model = EARShot_Model(
        excluded_Talker= exclusion_Talker,
        start_Epoch= start_Epoch,    #For restore
        extract_Dir= extract_Dir
        )
    new_EARS_Model.Restore(warning_Ignore=True)
    new_EARS_Model.Train()    
    #new_EARS_Model.Test(start_Epoch)